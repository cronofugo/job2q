# -*- coding: utf-8 -*-
import os
import sys
from time import time, sleep
from subprocess import CalledProcessError, call, check_output
from .details import wrappers
from . import dialogs, messages
from .queue import jobsubmit, jobstat
from .fileutils import AbsPath, NotAbsolutePath, splitpath, pathjoin, remove
from .utils import Bunch, DefaultDict, IdentityList, natkey, o, p, q, Q, join_args, booldict, interpolate
from .shared import names, paths, environ, sysconf, queuespecs, progspecs, options, remoteargs
from .parsing import BoolParser
from .readmol import readmol

parameterpaths = []
script = Bunch()

def initialize():

    script.main = []
    script.setup = []
    script.header = []
    script.envars = []

    for key, path in options.targetfiles.items():
        if not path.isfile():
            messages.error('El archivo de entrada', path, 'no existe', option=o(key))

    if options.remote.host:
        (paths.home/'.ssh').mkdir()
        paths.socket = paths.home / '.ssh' / pathjoin((options.remote.host, 'job2q', 'sock'))
        try:
            environment = check_output(['ssh', '-o', 'ControlMaster=auto', '-o', 'ControlPersist=60', '-S', paths.socket, options.remote.host, 'printenv JOBCOMMAND JOBSYNCDIR'])
        except CalledProcessError as e:
            messages.error(e.output.decode(sys.stdout.encoding).strip())
        options.remote.cmd, options.remote.root = environment.decode(sys.stdout.encoding).splitlines()
        if 'cmd' not in options.remote and 'root' not in options.remote:
            messages.error('El servidor no está configurado para aceptar trabajos')
        if 'cmd' not in options.remote or 'root' not in options.remote:
            messages.error('El servidor no está correctamente configurado para aceptar trabajos')

    if options.interpolation.vars or options.interpolation.mol or 'trjmol' in options.interpolation:
        options.interpolation.interpolate = True
    else:
        options.interpolation.interpolate = False

    if options.interpolation.interpolate:
        options.interpolation.list = []
        options.interpolation.dict = {}
        if options.interpolation.vars:
            for var in options.interpolation.vars:
                left, separator, right = var.partition(':')
                if separator:
                    if right:
                        options.interpolation.dict[left] = right
                    else:
                        messages.error('No se especificó ningín valor para la variable de interpolación', left)
                else:
                    options.interpolation.list.append(left)
        if options.interpolation.mol:
            index = 0
            for path in options.interpolation.mol:
                index += 1
                path = AbsPath(path, cwd=options.common.cwd)
                coords = readmol(path)[-1]
                options.interpolation.dict['mol' + str(index)] = '\n'.join('{0:<2s}  {1:10.4f}  {2:10.4f}  {3:10.4f}'.format(*atom) for atom in coords)
            if not 'prefix' in options.interpolation:
                if len(options.interpolation.mol) == 1:
                    options.prefix = path.stem
                else:
                    messages.error('Se debe especificar un prefijo cuando se especifican múltiples archivos de coordenadas')
        elif 'trjmol' in options.interpolation:
            index = 0
            path = AbsPath(options.interpolation.trjmol, cwd=options.common.cwd)
            for coords in readmol(path):
                index += 1
                options.interpolation.dict['mol' + str(index)] = '\n'.join('{0:<2s}  {1:10.4f}  {2:10.4f}  {3:10.4f}'.format(*atom) for atom in coords)
            if not 'prefix' in options.interpolation:
                options.prefix = path.stem
        else:
            if not 'prefix' in options.interpolation and not 'suffix' in options.interpolation:
                messages.error('Se debe especificar un prefijo o un sufijo para interpolar sin archivo coordenadas')

    try:
        sysconf.delay = float(sysconf.delay)
    except ValueError:
        messages.error('El tiempo de espera debe ser un numéro', conf='delay')
    except AttributeError:
        sysconf.delay = 0
    
    if not 'scratch' in sysconf.defaults:
        messages.error('No se especificó el directorio de escritura por defecto', spec='defaults.scratch')

    if 'scratch' in options.common:
        options.jobscratch = options.common.scratch / queuespecs.envars.jobid
    else:
        options.jobscratch = AbsPath(pathjoin(sysconf.defaults.scratch, keys=names)) / queuespecs.envars.jobid

    if 'queue' not in options.common:
        if 'queue' in sysconf.defaults:
            options.common.queue = sysconf.defaults.queue
        else:
            messages.error('Debe especificar la cola a la que desea enviar el trabajo')
    
    if not 'longname' in progspecs:
        messages.error('No se especificó el nombre del programa', spec='longname')
    
    if not 'shortname' in progspecs:
        messages.error('No se especificó el sufijo del programa', spec='shortname')
    
    for key in options.parameterkeys:
        if '/' in options.parameterkeys[key]:
            messages.error(options.parameterkeys[key], 'no puede ser una ruta', option=key)

    if 'mpilaunch' in progspecs:
        try: progspecs.mpilaunch = booldict[progspecs.mpilaunch]
        except KeyError:
            messages.error('Este valor requiere ser "True" o "False"', spec='mpilaunch')
    
    if not progspecs.filekeys:
        messages.error('La lista de archivos del programa no existe o está vacía', spec='filekeys')
    
    if progspecs.inputfiles:
        for key in progspecs.inputfiles:
            if not key in progspecs.filekeys:
                messages.error('La clave', q(key), 'no tiene asociado ningún archivo', spec='inputfiles')
    else:
        messages.error('La lista de archivos de entrada no existe o está vacía', spec='inputfiles')
    
    if progspecs.outputfiles:
        for key in progspecs.outputfiles:
            if not key in progspecs.filekeys:
                messages.error('La clave', q(key), 'no tiene asociado ningún archivo', spec='outputfiles')
    else:
        messages.error('La lista de archivos de salida no existe o está vacía', spec='outputfiles')

    if 'prefix' in options.interpolation:
        try:
            options.prefix = interpolate(
                options.interpolation.prefix,
                delimiter='%',
                keylist=options.interpolation.list,
                keydict=options.interpolation.dict,
            )
        except ValueError as e:
            messages.error('Hay variables de interpolación inválidas en el prefijo', opt='--prefix', var=e.args[0])
        except (IndexError, KeyError) as e:
            messages.error('Hay variables de interpolación sin definir en el prefijo', opt='--prefix', var=e.args[0])

    if 'suffix' in options.interpolation:
        try:
            options.suffix = interpolate(
                options.interpolation.suffix,
                delimiter='%',
                keylist=options.interpolation.list,
                keydict=options.interpolation.dict,
            )
        except ValueError as e:
            messages.error('Hay variables de interpolación inválidas en el sufijo', opt='--suffix', var=e.args[0])
        except (IndexError, KeyError) as e:
            messages.error('Hay variables de interpolación sin definir en el sufijo', opt='--suffix', var=e.args[0])

    if options.remote.host:
        return

    ############ Local execution ###########

    if 'jobinfo' in queuespecs:
        script.header.append(queuespecs.jobinfo.format(progspecs.longname))

    #TODO MPI support for Slurm
    if progspecs.parallelib:
        if progspecs.parallelib.lower() == 'none':
            if 'nodelist' in options.common:
                for item in queuespecs.serialat:
                    script.header.append(item.format(**options.common))
            else:
                for item in queuespecs.serial:
                    script.header.append(item.format(**options.common))
        elif progspecs.parallelib.lower() == 'openmp':
            if 'nodelist' in options.common:
                for item in queuespecs.singlehostat:
                    script.header.append(item.format(**options.common))
            else:
                for item in queuespecs.singlehost:
                    script.header.append(item.format(**options.common))
            script.main.append('OMP_NUM_THREADS=' + str(options.common.nproc))
        elif progspecs.parallelib.lower() == 'standalone':
            if 'nodelist' in options.common:
                for item in queuespecs.multihostat:
                    script.header.append(item.format(**options.common))
            else:
                for item in queuespecs.multihost:
                    script.header.append(item.format(**options.common))
        elif progspecs.parallelib.lower() in wrappers:
            if 'nodelist' in options.common:
                for item in queuespecs.multihostat:
                    script.header.append(item.format(**options.common))
            else:
                for item in queuespecs.multihost:
                    script.header.append(item.format(**options.common))
            script.main.append(queuespecs.mpilauncher[progspecs.parallelib])
        else:
            messages.error('El tipo de paralelización', progspecs.parallelib, 'no está soportado', spec='parallelib')
    else:
        messages.error('No se especificó el tipo de paralelización del programa', spec='parallelib')

    if not sysconf.versions:
        messages.error('La lista de versiones no existe o está vacía', spec='versions')

    for version in sysconf.versions:
        if not sysconf.versions[version].executable:
            messages.error('No se especificó el ejecutable', spec='versions[{}].executable'.format(version))
    
    if 'version' in options.common:
        if options.common.version not in sysconf.versions:
            messages.error('La versión', options.common.version, 'no es válida', option='version')
        options.version = options.common.version
    elif 'version' in sysconf.defaults:
        if not sysconf.defaults.version in sysconf.versions:
            messages.error('La versión establecida por defecto es inválida', spec='defaults.version')
        if options.common.interactive:
            options.version = dialogs.chooseone('Seleccione una versión:', choices=list(sysconf.versions.keys()), default=sysconf.defaults.version)
        else:
            options.version = sysconf.defaults.version
    else:
        options.version = dialogs.chooseone('Seleccione una versión:', choices=list(sysconf.versions.keys()))

    for envar, path in progspecs.export.items() | sysconf.versions[options.version].export.items():
        abspath = AbsPath(pathjoin(path, keys=names), cwd=options.jobscratch)
        script.setup.append('export {0}={1}'.format(envar, abspath))

    for envar, path in progspecs.append.items() | sysconf.versions[options.version].append.items():
        abspath = AbsPath(pathjoin(path, keys=names), cwd=options.jobscratch)
        script.setup.append('{0}={1}:${0}'.format(envar, abspath))

    for path in progspecs.source + sysconf.versions[options.version].source:
        script.setup.append('source {}'.format(AbsPath(pathjoin(path, keys=names))))

    if progspecs.load or sysconf.versions[options.version].load:
        script.setup.append('module purge')

    for module in progspecs.load + sysconf.versions[options.version].load:
        script.setup.append('module load {}'.format(module))

    try:
        script.main.append(AbsPath(pathjoin(sysconf.versions[options.version].executable, keys=names)))
    except NotAbsolutePath:
        script.main.append(sysconf.versions[options.version].executable)

    for path in queuespecs.logfiles:
        script.header.append(path.format(AbsPath(pathjoin(sysconf.logdir, keys=names))))

    script.setup.append("shopt -s nullglob extglob")

    script.setenv = '{}="{}"'.format

    script.envars.extend(queuespecs.envars.items())
    script.envars.extend((k + 'name', v) for k, v in names.items())
    script.envars.extend((k, progspecs.filekeys[v]) for k, v in progspecs.filevars.items())

    script.envars.append(("freeram", "$(free -m | tail -n+3 | head -1 | awk '{print $4}')"))
    script.envars.append(("totalram", "$(free -m | tail -n+2 | head -1 | awk '{print $2}')"))
    script.envars.append(("jobram", "$(($nproc*$totalram/$(nproc --all)))"))

    for key in progspecs.optargs:
        if not progspecs.optargs[key] in progspecs.filekeys:
            messages.error('La clave', q(key) ,'no tiene asociado ningún archivo', spec='optargs')
        script.main.append('-{key} {val}'.format(key=key, val=progspecs.filekeys[progspecs.optargs[key]]))
    
    for item in progspecs.posargs:
        for key in item.split('|'):
            if not key in progspecs.filekeys:
                messages.error('La clave', q(key) ,'no tiene asociado ningún archivo', spec='posargs')
        script.main.append('@' + p('|'.join(progspecs.filekeys[i] for i in item.split('|'))))
    
    if 'stdinput' in progspecs:
        try:
            script.main.append('0<' + ' ' + progspecs.filekeys[progspecs.stdinput])
        except KeyError:
            messages.error('La clave', q(progspecs.stdinput) ,'no tiene asociado ningún archivo', spec='stdinput')
    if 'stdoutput' in progspecs:
        try:
            script.main.append('1>' + ' ' + progspecs.filekeys[progspecs.stdoutput])
        except KeyError:
            messages.error('La clave', q(progspecs.stdoutput) ,'no tiene asociado ningún archivo', spec='stdoutput')
    if 'stderror' in progspecs:
        try:
            script.main.append('2>' + ' ' + progspecs.filekeys[progspecs.stderror])
        except KeyError:
            messages.error('La clave', q(progspecs.stderror) ,'no tiene asociado ningún archivo', spec='stderror')
    
    script.chdir = 'cd "{}"'.format
    if sysconf.filesync == 'local':
        script.rmdir = 'rm -rf "{}"'.format
        script.mkdir = 'mkdir -p -m 700 "{}"'.format
        if options.common.dispose:
            script.simport = 'mv "{}" "{}"'.format
        else:
            script.simport = 'cp "{}" "{}"'.format
        script.rimport = 'cp -r "{}/." "{}"'.format
        script.sexport = 'cp "{}" "{}"'.format
    elif sysconf.filesync == 'remote':
        script.rmdir = 'for host in ${{hostlist[*]}}; do rsh $host rm -rf "\'{}\'"; done'.format
        script.mkdir = 'for host in ${{hostlist[*]}}; do rsh $host mkdir -p -m 700 "\'{}\'"; done'.format
        if options.common.dispose:
            script.simport = 'for host in ${{hostlist[*]}}; do rcp $headname:"\'{0}\'" $host:"\'{1}\'" && rsh $headname rm "\'{0}\'"; done'.format
        else:
            script.simport = 'for host in ${{hostlist[*]}}; do rcp $headname:"\'{0}\'" $host:"\'{1}\'"; done'.format
        script.rimport = 'for host in ${{hostlist[*]}}; do rsh $headname tar -cf- -C "\'{0}\'" . | rsh $host tar -xf- -C "\'{1}\'"; done'.format
        script.sexport = 'rcp "{}" $headname:"\'{}\'"'.format
    elif sysconf.filesync == 'secure':
        script.rmdir = 'for host in ${{hostlist[*]}}; do ssh $host rm -rf "\'{}\'"; done'.format
        script.mkdir = 'for host in ${{hostlist[*]}}; do ssh $host mkdir -p -m 700 "\'{}\'"; done'.format
        if options.common.dispose:
            script.simport = 'for host in ${{hostlist[*]}}; do scp $headname:"\'{0}\'" $host:"\'{1}\'" && ssh $headname rm "\'{0}\'"; done'.format
        else:
            script.simport = 'for host in ${{hostlist[*]}}; do scp $headname:"\'{0}\'" $host:"\'{1}\'"; done'.format
        script.rimport = 'for host in ${{hostlist[*]}}; do ssh $headname tar -cf- -C "\'{0}\'" . | ssh $host tar -xf- -C "\'{1}\'"; done'.format
        script.sexport = 'scp "{}" $headname:"\'{}\'"'.format
    else:
        messages.error('El método de copia', q(sysconf.filesync), 'no es válido', spec='filesync')


def submit(parentdir, inputname, filtergroups):

    filebools = {key: AbsPath(pathjoin(parentdir, (inputname, key))).isfile() or key in options.targetfiles for key in progspecs.filekeys}
    for conflict, message in progspecs.conflicts.items():
        if BoolParser(conflict).evaluate(filebools):
            messages.error(message, p(inputname))

    if inputname.endswith('.' + progspecs.shortname):
        jobname = inputname[:-len(progspecs.shortname)-1]
    else:
        jobname = inputname

    if 'prefix' in options:
        jobname = options.prefix + '.' + jobname

    if 'suffix' in options:
        jobname = jobname +  '.' + options.suffix

    #TODO Append program version to output file extension if option is enabled
    if inputname.endswith('.' + progspecs.shortname):
        outputname = jobname + '.' + progspecs.shortname
    else:
        outputname = jobname

    if 'out' in options.common:
        outdir = AbsPath(options.common.out, cwd=parentdir)
    else:
        outdir = AbsPath(jobname, cwd=parentdir)

    literalfiles = {}
    interpolatedfiles = {}

    if options.common.raw:
        stagedir = parentdir
    else:
        if outdir == parentdir:
            messages.failure('El directorio de salida debe ser distinto al directorio padre')
            return
        stagedir = outdir
        for key in progspecs.inputfiles:
            srcpath = AbsPath(pathjoin(parentdir, (inputname, key)))
            destpath = pathjoin(stagedir, (outputname, key))
            if srcpath.isfile():
                if 'interpolable' in progspecs and key in progspecs.interpolable:
                    with open(srcpath, 'r') as f:
                        contents = f.read()
                        if options.interpolation.interpolate:
                            try:
                                interpolatedfiles[destpath] = interpolate(
                                    contents,
                                    delimiter=options.interpolation.delimiter,
                                    keylist=options.interpolation.list,
                                    keydict=options.interpolation.dict,
                                )
                            except ValueError:
                                messages.failure('Hay variables de interpolación inválidas en el archivo de entrada', pathjoin((inputname, key)))
                                return
                            except (IndexError, KeyError) as e:
                                messages.failure('Hay variables de interpolación sin definir en el archivo de entrada', pathjoin((inputname, key)), key=e.args[0])
                                return
                        else:
                            try:
                                interpolatedfiles[destpath] = interpolate(contents, delimiter=options.interpolation.delimiter)
                            except ValueError:
                                pass
                            except (IndexError, KeyError) as e:
                                if dialogs.yesno('Parece que hay variables de interpolación en el archivo de entrada', pathjoin((inputname, key)),'¿desea continuar sin interpolar?'):
                                    literalfiles[destpath] = srcpath
                                else:
                                    return
                else:
                    literalfiles[destpath] = srcpath

    jobdir = AbsPath(pathjoin(stagedir, (jobname, progspecs.shortname, 'job')))

    if outdir.isdir():
        if jobdir.isdir():
            try:
                with open(pathjoin(jobdir, 'id'), 'r') as f:
                    jobid = f.read()
                jobstate = jobstat(jobid)
                if jobstate is not None:
                    messages.failure(jobstate.format(id=jobid, name=jobname))
                    return
            except FileNotFoundError:
                pass
        if not set(outdir.listdir()).isdisjoint(pathjoin((outputname, k)) for k in progspecs.outputfiles):
            if options.common.no or (not options.common.yes and not dialogs.yesno('Si corre este cálculo los archivos de salida existentes en el directorio', outdir,'serán sobreescritos, ¿desea continuar de todas formas?')):
                messages.failure('Cancelado por el usuario')
                return
        for key in progspecs.outputfiles:
            remove(pathjoin(outdir, (outputname, key)))
        if parentdir != outdir:
            for key in progspecs.inputfiles:
                remove(pathjoin(outdir, (outputname, key)))
    else:
        try:
            outdir.makedirs()
        except FileExistsError:
            messages.failure('No se puede crear la carpeta', outdir, 'porque ya existe un archivo con ese nombre')
            return

    for destpath, litfile in literalfiles.items():
        litfile.copyto(destpath)

    for destpath, contents in interpolatedfiles.items():
        with open(destpath, 'w') as f:
            f.write(contents)

    for key, targetfile in options.targetfiles.items():
        targetfile.linkto(pathjoin(stagedir, (outputname, progspecs.fileoptions[key])))

    if options.remote.host:

        reloutdir = os.path.relpath(outdir, paths.home)
        remotehome = pathjoin(options.remote.root, (names.user, names.host))
        remotetemp = pathjoin(options.remote.root, (names.user, names.host, 'temp'))
        remoteargs.switches.add('raw')
        remoteargs.switches.add('jobargs')
        remoteargs.switches.add('dispose')
        remoteargs.constants['cwd'] = pathjoin(remotetemp, reloutdir)
        remoteargs.constants['out'] = pathjoin(remotehome, reloutdir)
        for key, value in options.parameterkeys.items():
            remoteargs.constants[key] = interpolate(value, delimiter='%', keylist=filtergroups)
        filelist = []
        for key in progspecs.filekeys:
            if os.path.isfile(pathjoin(outdir, (outputname, key))):
                filelist.append(pathjoin(paths.home, '.', reloutdir, (outputname, key)))
        arglist = ['ssh', '-qt', '-S', paths.socket, options.remote.host]
        arglist.extend(env + '=' + val for env, val in environ.items())
        arglist.append(options.remote.cmd)
        arglist.append(names.program)
        arglist.extend(o(opt) for opt in remoteargs.switches)
        arglist.extend(o(opt, Q(val)) for opt, val in remoteargs.constants.items())
        arglist.extend(o(opt, Q(val)) for opt, lst in remoteargs.lists.items() for val in lst)
        arglist.append(jobname)
        if options.debug.dryrun:
            print('<FILE LIST>', ' '.join(filelist), '</FILE LIST>')
            print('<COMMAND LINE>', ' '.join(arglist[3:]), '</COMMAND LINE>')
        else:
            try:
                check_output(['rsync', '-e', "ssh -S '{}'".format(paths.socket), '-qRLtz'] + filelist + [options.remote.host + ':' + remotetemp])
                check_output(['rsync', '-e', "ssh -S '{}'".format(paths.socket), '-qRLtz', '-f', '-! */'] + filelist + [options.remote.host + ':' + remotehome])
            except CalledProcessError as e:
                messages.error(e.output.decode(sys.stdout.encoding).strip())
            call(arglist)

        return

    ############ Local execution ###########

    parameterkeys = DefaultDict()
    defaultparameterkeys = DefaultDict()

    for key, value in sysconf.defaults.parameterkeys.items():
        try:
            defaultparameterkeys[key] = interpolate(value, delimiter='%', keylist=filtergroups)
        except ValueError:
            messages.error('Hay variables de interpolación inválidas en la opción por defecto', key)
        except IndexError:
            messages.error('Hay variables de interpolación sin definir en la opción por defecto', key)

    for key, value in options.parameterkeys.items():
        try:
            parameterkeys[key] = interpolate(value, delimiter='%', keylist=filtergroups)
        except ValueError:
            messages.error('Hay variables de interpolación inválidas en la opción', key)
        except IndexError:
            messages.error('Hay variables de interpolación sin definir en la opción', key)

    if not options.common.interactive:
        parameterkeys.update(defaultparameterkeys)

    for path in sysconf.parameterpaths:
        parts = splitpath(pathjoin(path, keys=names))
        trunk = AbsPath(parts.pop(0))
        for part in parts:
            if not trunk.isdir():
                messages.error(trunk.failreason)
            part = part.format_map(parameterkeys)
            if parameterkeys._keys:
                choices = trunk.glob(part.format_map(DefaultDict('*')))
                if choices:
                    if options.common.interactive:
                        default = trunk.glob(part.format_map(defaultparameterkeys).format_map(DefaultDict('*')))[0]
                        choice = dialogs.chooseone('Seleccione un directorio de', trunk, choices=choices, default=default)
                    else:
                        choice = dialogs.chooseone('Seleccione un directorio de', trunk, choices=choices)
                    trunk = trunk / choice
                else:
                    messages.error(trunk, 'no contiene elementos coincidentes con la ruta', path)
            else:
                trunk = trunk / part
        parameterpaths.append(trunk)
    print(parameterpaths)

    imports = []
    exports = []

    for key in progspecs.inputfiles:
        if AbsPath(pathjoin(parentdir, (inputname, key))).isfile():
            imports.append(script.simport(pathjoin(stagedir, (outputname, key)), pathjoin(options.jobscratch, progspecs.filekeys[key])))

    for key in options.targetfiles:
        imports.append(script.simport(pathjoin(stagedir, (outputname, progspecs.fileoptions[key])), pathjoin(options.jobscratch, progspecs.filekeys[progspecs.fileoptions[key]])))

    for path in parameterpaths:
        if path.isfile():
            imports.append(script.simport(path, pathjoin(options.jobscratch, path.name)))
        elif path.isdir():
            imports.append(script.rimport(pathjoin(path), options.jobscratch))
        else:
            messages.error('La ruta de parámetros', path, 'no existe')

    for key in progspecs.outputfiles:
        exports.append(script.sexport(pathjoin(options.jobscratch, progspecs.filekeys[key]), pathjoin(outdir, (outputname, key))))

    try:
        jobdir.mkdir()
    except FileExistsError:
        messages.failure('No se puede crear la carpeta', jobdir, 'porque ya existe un archivo con ese nombre')
        return

    jobscript = pathjoin(jobdir, 'script')

    with open(jobscript, 'w') as f:
        f.write('#!/bin/bash' + '\n')
        f.write(queuespecs.jobname.format(jobname) + '\n')
        f.write(''.join(i + '\n' for i in script.header))
        f.write(''.join(i + '\n' for i in script.setup))
        f.write(''.join(script.setenv(i, j) + '\n' for i, j in script.envars))
        f.write(script.setenv('jobname', jobname) + '\n')
        f.write('for host in ${hostlist[*]}; do echo "<$host>"; done' + '\n')
        f.write(script.mkdir(options.jobscratch) + '\n')
        f.write(''.join(i + '\n' for i in imports))
        f.write(script.chdir(options.jobscratch) + '\n')
        f.write(''.join(i + '\n' for i in progspecs.prescript))
        f.write(' '.join(script.main) + '\n')
        f.write(''.join(i + '\n' for i in progspecs.postscript))
        f.write(''.join(i + '\n' for i in exports))
        f.write(script.rmdir(options.jobscratch) + '\n')
        f.write(''.join(i + '\n' for i in sysconf.offscript))

    if options.debug.dryrun:
        messages.success('Se procesó el trabajo', q(jobname), 'y se generaron los archivos para el envío en', jobdir)
    else:
        try:
            sleep(sysconf.delay + options.common.delay + os.stat(paths.lock).st_mtime - time())
        except (ValueError, FileNotFoundError) as e:
            pass
        try:
            jobid = jobsubmit(jobscript)
        except RuntimeError as error:
            messages.failure('El gestor de trabajos reportó un error al enviar el trabajo', q(jobname), p(error))
            return
        else:
            messages.success('El trabajo', q(jobname), 'se correrá en', str(options.common.nproc), 'núcleo(s) en', names.cluster, 'con número de trabajo', jobid)
            with open(pathjoin(jobdir, 'id'), 'w') as f:
                f.write(jobid)
            with open(paths.lock, 'a'):
                os.utime(paths.lock, None)

