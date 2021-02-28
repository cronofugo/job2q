# -*- coding: utf-8 -*-
import sys
import os
from re import search
from subprocess import Popen, PIPE
from .shared import jobspecs

def submitjob(jobscript):
    with open(jobscript, 'r') as fh:
        process = Popen(queuespecs.submitcmd, stdin=fh, stdout=PIPE, stderr=PIPE, close_fds=True)
    output, error = process.communicate()
    output = output.decode(sys.stdout.encoding).strip()
    error = error.decode(sys.stdout.encoding).strip()
    if process.returncode == 0:
        return search(queuespecs.idregex, output).group(1)
    else:
        raise RuntimeError(error)
        
def checkjob(jobid):
    process = Popen(queuespecs.statcmd + [jobid], stdout=PIPE, stderr=PIPE, close_fds=True)
    output, error = process.communicate()
    output = output.decode(sys.stdout.encoding).strip()
    error = error.decode(sys.stdout.encoding).strip()
    if process.returncode == 0:
        if output in queuespecs.blocking_states:
            return queuespecs.blocking_states[output]
        elif output in queuespecs.ready_states:
            return None
        else:
            return 'El trabajo "{name}" no se envió porque su estado no está registrado: ' + output.strip()
    else:
        return 'El trabajo "{name}" no se envió porque ocurrió error al revisar su estado: ' + error
       
