# -*- coding: utf-8 -*-

from __future__ import unicode_literals
from __future__ import print_function
from __future__ import absolute_import

from socket import gethostname, gethostbyname
from os import path, listdir, mkdir, rename
from tempfile import NamedTemporaryFile
from subprocess import call
from re import sub

from jobToQueue.utils import post, textform, pathjoin, quote, prompt, copyfile, remove
from jobToQueue.parse import parse_boolexpr
from jobToQueue.classes import ec, it, sc

def queuejob(sysconf, jobconf, options, scheduler, inputfile):

    jobcontrol = [ ]
    exportfiles = [ ]
    importfiles = [ ]
    redirections = [ ]
    environment = [ ]
    arguments = [ ]
    filebool = { }

    jobname = sc.null
    filename = path.basename(inputfile)
    master = gethostbyname(gethostname())
    localdir = path.abspath(path.dirname(inputfile))
    version = sc.null.join(c.lower() for c in options.version if c.isalnum())

    for ext in jobconf.inputfiles:
        try: jobconf.fileexts[ext]
        except KeyError: post('La extensión de archivo de salida', ext, 'no fue definida', kind=ec.cfgerr)

    for ext in jobconf.outputfiles:
        try: jobconf.fileexts[ext]
        except KeyError: post('La extensión de archivo de salida', ext, 'no fue definida', kind=ec.cfgerr)

    if 'versionprefix' in jobconf:
        version = jobconf.versionprefix + version
    else:
        version = 'v' + version

    iosuffix = { ext : version + sc.dot + ext for ext in jobconf.fileexts }

    if not jobname:
        for ext in jobconf.inputfiles:
            if filename.endswith(sc.dot + ext):
                jobname = filename[:-len(sc.dot + ext)]
                ionames = { ext : ext for ext in jobconf.fileexts }
                break

    if not jobname:
        post('La extensión', path.splitext(filename)[1], 'no es de un archivo de entrada de', jobconf.title, kind=ec.joberr)
        return

    for ext in ionames:
        filebool[ext] = path.isfile(pathjoin(localdir, [jobname, ionames[ext]]))
        #print(pathjoin([jobname, ionames[ext]]), filebool[ext])

    if 'filecheck' in jobconf:
        if not parse_boolexpr(jobconf.filecheck, filebool):
            post('No existen algunos de los archivos de entrada requeridos', kind=ec.joberr)
            return

    if 'fileclash' in jobconf:
        if parse_boolexpr(jobconf.fileclash, filebool):
            post('Hay un conflicto entre algunos de los archivos de entrada', kind=ec.joberr)
            return

    if jobconf.outputdir is True:
        outputdir = pathjoin(localdir, jobname)
    else: outputdir = localdir

    for var in jobconf.get('filevars', []):
        environment.append(var + '=' + jobconf.fileexts[jobconf.filevars[var]])

    environment.extend(sysconf.get('init', []))
    environment.extend(scheduler.environment)

    environment.append("shopt -s nullglob extglob")
    environment.append("workdir=" + options.scratch + "/" + scheduler.jobidvar)
    environment.append("freeram=$(free -m | tail -n+3 | head -1 | awk '{print $4}')")
    environment.append("totalram=$(free -m | tail -n+2 | head -1 | awk '{print $2}')")
    environment.append("jobram=$(($ncpu*$totalram/$(nproc --all)))")

    #TODO: Test if parameter directory exists in the filesystem

    jobcontrol.append(scheduler.jobname.format(jobname))
    jobcontrol.append(scheduler.label.format(jobconf.title))
    jobcontrol.append(scheduler.queue.format(options.queue))

    if options.exechost is not None: 
        jobcontrol.append(scheduler.host.format(options.exechost))

    if sysconf.storage == 'pooled':
         jobcontrol.append(scheduler.stdout.format(pathjoin(options.scratch, [scheduler.jobid, 'out'])))
         jobcontrol.append(scheduler.stderr.format(pathjoin(options.scratch, [scheduler.jobid, 'err'])))
    elif sysconf.storage == 'shared':
         jobcontrol.append(scheduler.stdout.format(pathjoin(outputdir, [scheduler.jobid, 'out'])))
         jobcontrol.append(scheduler.stderr.format(pathjoin(outputdir, [scheduler.jobid, 'err'])))
    else:
         post(sysconf.storage + ' no es un tipo de almacenamiento soportado por este script', kind=ec.cfgerr)

    jobcommand = jobconf.program.executable

    #TODO: MPI support for Slurm
    if jobconf.runtype == 'serial':
        jobcontrol.append(scheduler.ncpu.format(1))
    elif jobconf.runtype == 'openmp':
        jobcontrol.append(scheduler.ncpu.format(options.ncpu))
        jobcontrol.append(scheduler.span.format(1))
        environment.append('export OMP_NUM_THREADS=' + str(options.ncpu))
    elif jobconf.runtype in ['openmpi','intelmpi','mpich']:
        jobcontrol.append(scheduler.ncpu.format(options.ncpu))
        if options.nodes is not None:
            jobcontrol.append(scheduler.span.format(options.nodes))
        if jobconf.mpiwrapper is True:
            jobcommand = scheduler.mpiwrapper[jobconf.runtype] + sc.ws + jobcommand
    else: post('El tipo de paralelización ' + jobconf.runtype + ' no es válido', kind=ec.cfgerr)

    for ext in jobconf.inputfiles:
        importfiles.append(['ssh', master, 'scp', quote(quote(pathjoin(outputdir, [jobname, iosuffix[ext]]))), \
           '$ip:' + quote(quote(pathjoin('$workdir', jobconf.fileexts[ext])))])

    for ext in jobconf.inputfiles + jobconf.outputfiles:
        exportfiles.append(['scp', quote(pathjoin('$workdir', jobconf.fileexts[ext])), \
            master + ':' + quote(quote(pathjoin(outputdir, [jobname, iosuffix[ext]])))])

    for parset in jobconf.parsets:
        if not path.isabs(parset):
            parset = pathjoin(localdir, parset)
        if path.isdir(parset):
            parset = pathjoin(parset, sc.dot)
        importfiles.append(['ssh', master, 'scp -r', quote(quote(parset)), '$ip:' + quote(quote('$workdir'))])

    for profile in jobconf.setdefault('profile', []) + jobconf.program.setdefault('profile', []):
        environment.append(profile)

    if 'stdin' in jobconf:
        try: redirections.append('0<' + sc.ws + jobconf.fileexts[jobconf.stdin])
        except KeyError: post('El nombre de archivo "' + jobconf.stdin + '" en el tag <stdin> no fue definido.', kind=ec.cfgerr)
    if 'stdout' in jobconf:
        try: redirections.append('1>' + sc.ws + jobconf.fileexts[jobconf.stdout])
        except KeyError: post('El nombre de archivo "' + jobconf.stdout + '" en el tag <stdout> no fue definido.', kind=ec.cfgerr)
    if 'stderr' in jobconf:
        try: redirections.append('2>' + sc.ws + jobconf.fileexts[jobconf.stderr])
        except KeyError: post('El nombre de archivo "' + jobconf.stderr + '" en el tag <stderr> no fue definido.', kind=ec.cfgerr)

    if 'positionargs' in jobconf:
        for item in jobconf.positionargs:
            for ext in item.split('|'):
                if filebool[ext]:
                    arguments.append(jobconf.fileexts[ext])
                    break

    if 'optionargs' in jobconf:
        for opt in jobconf.optionargs:
            ext = jobconf.optionargs[opt]
            arguments.append('-' + opt + sc.ws + jobconf.fileexts[ext])

    jobdir = pathjoin(outputdir, [sc.null, jobname, version])

    try: mkdir(jobdir)
    except OSError:
        if path.isdir(outputdir):
            if path.isdir(jobdir):
                try:
                    lastjob = max(listdir(jobdir), key=int)
                except ValueError:
                    pass
                else:
                    jobstate = scheduler.checkjob(lastjob)
                    if jobstate in scheduler.jobstates:
                        post('El trabajo', quote(jobname), 'no se envió porque', scheduler.jobstates[jobstate], \
                            '(job {})'.format(lastjob), kind=ec.joberr)
                        return
            elif path.exists(jobdir):
                remove(jobdir)
                mkdir(jobdir)
            if not set(listdir(outputdir)).isdisjoint([ pathjoin([jobname, iosuffix[ext]]) for ext in jobconf.outputfiles ]):
                if options.defaultanswer is None:
                    options.defaultanswer = prompt('Si corre este cálculo los archivos de salida existentes en el directorio', outputdir,'serán sobreescritos, ¿desea continuar de todas formas (si/no)?', kind=it.yn)
                if options.defaultanswer is False:
                    post('El trabajo', quote(jobname), 'no se envió por solicitud del usuario', kind=ec.joberr)
                    return
            for ext in jobconf.inputfiles + jobconf.outputfiles:
                remove(pathjoin(outputdir, [jobname, iosuffix[ext]]))
        elif path.exists(outputdir):
            post('No se puede crear la carpeta', outputdir, 'porque hay un archivo con ese mismo nombre', kind=ec.joberr)
            return
        else:
            mkdir(outputdir)
            mkdir(jobdir)

    try:
        #TODO textform: do not write newlines or spaces if lists are empty
        fh = NamedTemporaryFile(mode='w+t', delete=False)
        fh.write(textform([textform(line, end=sc.null) for line in jobcontrol], sep=sc.nl))
        fh.write(textform(environment, sep=sc.nl))
        fh.write(textform('for ip in ${iplist[*]}; do'))
        fh.write(textform('ssh', master, 'ssh $ip mkdir -m 700', quote(quote('$workdir')), indent=4))
        fh.write(textform([textform(line, indent=4, end=sc.null) for line in importfiles], sep=sc.nl))
        fh.write(textform('done'))
        fh.write(textform('cd', quote('$workdir')))
        fh.write(textform([i.repr for i in jobconf.prescript if 'iff' not in i or filebool[i.iff]], sep=sc.nl)) \
            if 'prescript' in jobconf else None
        fh.write(textform(jobcommand, arguments, redirections))
        fh.write(textform([textform(line, end=sc.null) for line in exportfiles], sep=sc.nl))
        fh.write(textform('for ip in ${iplist[*]}; do'))
        fh.write(textform('ssh $ip rm -f', quote(quote('$workdir') + '/*'), indent=4))
        fh.write(textform('ssh $ip rmdir', quote(quote('$workdir')), indent=4))
        fh.write(textform('done'))
        if 'offscript' in sysconf:
            for command in sysconf.offscript:
                if not call(['sh', '-c', 'test ${{{}+?}}'.format(command.ifv)]):
                    fh.write(textform('ssh', master, '"{}"'.format(command.repr)))
    finally:
        fh.close()

    for ext in jobconf.inputfiles:
        if path.isfile(pathjoin(localdir, [jobname, ionames[ext]])):
            copyfile(pathjoin(localdir, [jobname, ionames[ext]]), pathjoin(outputdir, [jobname, iosuffix[ext]]))

    try:
        jobid = scheduler.submit(fh.name)
    except RuntimeError as e:
        post('El sistema de colas rechazó el trabajo', quote(jobname), 'con el mensaje', quote(e.args[0]), kind=ec.joberr)
    else:
        post('El trabajo', quote(jobname), 'se correrá en', str(options.ncpu), 'núcleo(s) de CPU (job', jobid + ')', kind=ec.sucess)
        copyfile(fh.name, pathjoin(jobdir, jobid))
        remove(fh.name)

