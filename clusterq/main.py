import sys, os, re
from socket import gethostname
#from tkdialogs import messages
from clinterface import messages, _
from argparse import ArgumentParser, Action, SUPPRESS
from .utils import AttrDict, LogDict, GlobDict, ConfigTemplate, InterpolationTemplate, option, readspec, natural_sorted as sorted, catch_keyboard_interrupt
from .fileutils import AbsPath, file_except_info
from .shared import names, nodes, paths, environ, config, options
from .parsing import BoolParser
from .submission import submit

class ArgList:
    def __init__(self, args):
        self.current = None
        if options.arguments.sort:
            self.args = sorted(args)
        elif options.arguments.sort_reverse:
            self.args = sorted(args, reverse=True)
        else:
            self.args = args
        if 'filter' in options.arguments:
            self.filter = re.compile(options.arguments.filter)
        else:
            self.filter = re.compile('.+')
    def __iter__(self):
        return self
    def __next__(self):
        try:
            self.current = self.args.pop(0)
        except IndexError:
            raise StopIteration
        if options.common.job:
            workdir = AbsPath(options.common.cwd)
            for key in config.inputfiles:
                if (workdir/self.current*key).isfile():
                    inputname = self.current
                    break
            else:
                messages.failure(_('No hay archivos de entrada del trabajo $job', job=self.current))
                return next(self)
        else:
            path = AbsPath(self.current, parent=options.common.cwd)
            try:
                path.assertfile()
            except Exception as e:
                file_except_info(e, path)
                return next(self)
            for key in config.inputfiles:
                if path.name.endswith('.' + key):
                    inputname = path.name[:-len('.' + key)]
                    break
            else:
                messages.failure(_('$file no es un archivo de entrada de $program', file=path.name, program=config.progname))
                return next(self)
            workdir = path.parent()
        filestatus = {}
        for key in config.filekeys:
            path = workdir/inputname*key
            filestatus[key] = path.isfile() #or key in options.restartfiles
        for conflict, message in config.conflicts.items():
            if BoolParser(conflict).evaluate(filestatus):
                messages.failure(InterpolationTemplate(message).safe_substitute(file=inputname))
                return next(self)
        matched = self.filter.fullmatch(inputname)
        if matched:
            filtergroups = {str(i): x for i, x in enumerate(matched.groups())}
            return workdir, inputname, filtergroups
        else:
            return next(self)

class ListOptions(Action):
    def __init__(self, **kwargs):
        super().__init__(nargs=0, **kwargs)
    def __call__(self, parser, namespace, values, option_string=None):
        if config.versions:
            print('Versiones disponibles:')
            default = config.defaults.version if 'version' in config.defaults else None
            print_tree(tuple(config.versions.keys()), [default], level=1)
        for path in config.parameterpaths:
            dirtree = {}
            path = ConfigTemplate(path).safe_substitute(names)
            dirbranches(AbsPath(), AbsPath(path).parts, dirtree)
            if dirtree:
                print('Conjuntos de parámetros disponibles:')
                print_tree(dirtree, level=1)
        raise SystemExit

class StorePath(Action):
    def __init__(self, **kwargs):
        super().__init__(nargs=1, **kwargs)
    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, AbsPath(values[0], parent=os.getcwd()))

#TODO How to append value to list?
class AppendPath(Action):
    def __init__(self, **kwargs):
        super().__init__(nargs=1, **kwargs)
    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, AbsPath(values[0], parent=os.getcwd()))

def dirbranches(trunk, componentlist, dirtree):
    trunk.assertdir()
    if componentlist:
        defaultdict = LogDict()
        component = ConfigTemplate(componentlist.pop(0)).substitute(defaultdict)
        if defaultdict.logged_keys:
            branches = trunk.glob(ConfigTemplate(component).substitute(GlobDict()))
            for branch in branches:
                dirtree[branch] = {}
                dirbranches(trunk/branch, componentlist, dirtree[branch])
        else:
            dirbranches(trunk/component, componentlist, dirtree)


@catch_keyboard_interrupt
def run():

    paths.cfgdir = AbsPath(os.environ['CLUSTERQCFG'])
    names.command = os.path.basename(sys.argv[1])
    sys.argv.pop(1)

    config.merge(readspec(paths.cfgdir/'profiles'/'__cluster__.json5'))
    config.merge(readspec(paths.cfgdir/'profiles'/names.command*'json5'))
    config.merge(readspec(paths.cfgdir/'progspecs'/config.progspecfile))
    config.merge(readspec(paths.cfgdir/'queuespecs'/config.queuespecfile))

    userconfdir = paths.home/'.clusterq'
    userclusterconf = userconfdir/'__cluster__.json5'
    userpackageconf = userconfdir/names.command*'json5'
    
    try:
        config.merge(readspec(userclusterconf))
    except FileNotFoundError:
        pass

    try:
        config.merge(readspec(userpackageconf))
    except FileNotFoundError:
        pass

    try:
        config.progname
    except AttributeError:
        messages.error(_('No se definió el nombre del programa'))

    try:
        config.displayname
    except AttributeError:
        messages.error(_('No se definió el nombre del programa para mostrar'))

    try:
        names.cluster = config.clustername
    except AttributeError:
        messages.error(_('No se definió el nombre del clúster'))

    try:
        nodes.head = config.headnode
    except AttributeError:
        nodes.head = names.host

    parser = ArgumentParser(prog=names.command, add_help=False, description='Envía trabajos de {} a la cola de ejecución.'.format(config.displayname))

    group1 = parser.add_argument_group('Argumentos')
    group1.add_argument('files', nargs='*', metavar='FILE', help='Rutas de los archivos de entrada.')

#    group1 = parser.add_argument_group('Ejecución remota')

    group2 = parser.add_argument_group('Opciones comunes')
    group2.name = 'common'
    group2.add_argument('-h', '--help', action='help', help='Mostrar este mensaje de ayuda y salir.')
    group2.add_argument('-l', '--list', action=ListOptions, default=SUPPRESS, help='Mostrar las opciones disponibles y salir.')
    group2.add_argument('-v', '--version', metavar='VERSION', default=SUPPRESS, help='Usar la versión VERSION del ejecutable.')
    group2.add_argument('-p', '--prompt', action='store_true', help='Seleccionar interactivamente las opciones disponibles.')
    group2.add_argument('-n', '--nproc', type=int, metavar='#PROCS', default=1, help='Requerir #PROCS núcleos de procesamiento.')
    group2.add_argument('-q', '--queue', metavar='QUEUE', default=SUPPRESS, help='Requerir la cola QUEUE.')
    group2.add_argument('-j', '--job', action='store_true', help='Interpretar los argumentos como nombres de trabajo en vez de rutas de archivo.')
    group2.add_argument('-o', '--out', action=StorePath, metavar='PATH', default=SUPPRESS, help='Escribir los archivos de salida en el directorio PATH.')
    group2.add_argument('--cwd', action=StorePath, metavar='PATH', default=os.getcwd(), help='Usar PATH como directorio actual de trabajo.')
    group2.add_argument('--raw', action='store_true', help='No interpolar ni crear copias de los archivos de entrada.')
    group2.add_argument('--move', action='store_true', help='Mover los archivos de entrada al directorio de salida en vez de copiarlos.')
    group2.add_argument('--scratch', action=StorePath, metavar='PATH', default=SUPPRESS, help='Escribir los archivos temporales en el directorio PATH.')
    hostgroup = group2.add_mutually_exclusive_group()
    hostgroup.add_argument('-N', '--nhost', type=int, metavar='#NODES', default=1, help='Requerir #NODES nodos de ejecución.')
    hostgroup.add_argument('-H', '--hosts', metavar='NODE', default=SUPPRESS, help='Solicitar nodos específicos de ejecución.')
    yngroup = group2.add_mutually_exclusive_group()
    yngroup.add_argument('--yes', action='store_true', help='Responder "si" a todas las preguntas.')
    yngroup.add_argument('--no', action='store_true', help='Responder "no" a todas las preguntas.')

    group3 = parser.add_argument_group('Opciones remotas')
    group3.name = 'remote'
    group3.add_argument('-R', '--remote-host', metavar='HOSTNAME', help='Procesar el trabajo en el host HOSTNAME.')

    group4 = parser.add_argument_group('Opciones de selección de archivos')
    group4.name = 'arguments'
    sortgroup = group4.add_mutually_exclusive_group()
    sortgroup.add_argument('-s', '--sort', action='store_true', help='Ordenar los argumentos en orden ascendente.')
    sortgroup.add_argument('-S', '--sort-reverse', action='store_true', help='Ordenar los argumentos en orden descendente.')
    group4.add_argument('-f', '--filter', metavar='REGEX', default=SUPPRESS, help='Enviar únicamente los trabajos que coinciden con la expresión regular.')
#    group4.add_argument('-r', '--restart-file', dest='restartfiles', metavar='FILE', action='append', default=[], help='Restart file path.')

    group5 = parser.add_argument_group('Opciones de interpolación')
    group5.name = 'interpolation'
    fixgroup = group5.add_mutually_exclusive_group()
    fixgroup.add_argument('--prefix', metavar='PREFIX', default=None, help='Agregar el prefijo PREFIX al nombre del trabajo.')
    fixgroup.add_argument('--suffix', metavar='SUFFIX', default=None, help='Agregar el sufijo SUFFIX al nombre del trabajo.')
    molgroup = group5.add_mutually_exclusive_group()
    molgroup.add_argument('-m', '--mol', metavar='MOLFILE', action='append', default=[], help='Incluir el último paso del archivo MOLFILE en las variables de interpolación.')
    molgroup.add_argument('-M', '--trjmol', metavar='MOLFILE', default=None, help='Incluir todos los pasos del archivo MOLFILE en las variables de interpolación.')
    group5.add_argument('-x', '--var', dest='posvars', metavar='VALUE', action='append', default=[], help='Variables posicionales de interpolación.')

    group7 = parser.add_argument_group('Opciones de depuración')
    group7.name = 'debug'
    group7.add_argument('--dry-run', action='store_true', help='Procesar los archivos de entrada sin enviar el trabajo.')

    group8 = parser.add_argument_group('Conjuntos de parámetros')
    group8.name = 'parameteropts'
    for key in config.parameteropts:
        group8.add_argument(option(key), metavar='SETNAME', default=SUPPRESS, help='Conjuntos de parámetros.')

    group9 = parser.add_argument_group('Variables de interpolación')
    group9.name = 'interpolopts'
    for key in config.interpolopts:
        group9.add_argument(option(key), metavar='VARNAME', default=SUPPRESS, help='Variables de interpolación.')

    parsedargs = parser.parse_args()
#    print(parsedargs)

    for group in parser._action_groups:
        group_dict = {a.dest:getattr(parsedargs, a.dest) for a in group._group_actions if a.dest in parsedargs}
        if hasattr(group, 'name'):
            options[group.name] = AttrDict(**group_dict)

    if not parsedargs.files:
        messages.error(_('Debe especificar al menos un archivo de entrada'))

    arguments = ArgList(parsedargs.files)

    try:
        environ.TELEGRAM_BOT_URL = os.environ['TELEGRAM_BOT_URL']
        environ.TELEGRAM_CHAT_ID = os.environ['TELEGRAM_CHAT_ID']
    except KeyError:
        pass

    for workdir, inputname, filtergroups in arguments:
        submit(workdir, inputname, filtergroups)
    

if __name__ == '__main__':
    run()
