from __future__ import print_function
#!/usr/bin/env python
import time, sys
from pyoptsparse import Optimization
import os, argparse
import numpy
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--sens",help="sensitivity mode",type=str, default='FD')
parser.add_argument("--constrained",help="constrained or not",type=int,default=0)
parser.add_argument("--testHist",help="test history",type=str,default="no")
parser.add_argument("--groups",help="use groups",type=int, default=0)
parser.add_argument("--sensMode",help="gradient mode",type=str, default='')
parser.add_argument("--opt",help="optimizer",type=str, default='SNOPT')
args = parser.parse_args()
sens = args.sens
constrained = args.constrained
testHist = args.testHist
groups = args.groups
sensMode = args.sensMode
if args.opt == 'IPOPT':
    from pyoptsparse import IPOPT as OPT
else:
    from pyoptsparse import SNOPT as OPT

def objfunc(xx):
    if groups:
        x = xx['x'] # Extract array
    else:
        x = xx

    fobj = 100*(x[1]-x[0]**2)**2+(1-x[0])**2

    fcon = {}
    fcon['con'] = 0.1-(x[0]-1)**3 - (x[1]-1)


    fail = False

    return fobj, fcon, fail

def sensfunc(xx, fobj, fcon):
    if groups:
        x = xx['x'] # Extract array
    else:
        x = xx

    gobj = {}
    gobj['xvars'] = [2*100*(x[1]-x[0]**2)*(-2*x[0]) - 2*(1-x[0]),
                     2*100*(x[1]-x[0]**2)]
    gcon = {}
    gcon['con'] = {'xvars':[-3*(x[0]-1)**2, -1]}
    fail = False

    return gobj, gcon, fail

if sens == 'none':
    sens = None
if sens == 'user':
    sens = sensfunc

# Instantiate Optimization Problem
optProb = Optimization('Rosenbrock function', objfunc, useGroups=groups)
optProb.addVarGroup('x', 2, 'c', value=[5,5], lower=-5.12, upper=5.12,
                    scale=[1.0, 1.0], varSet='xvars')
if constrained:
    optProb.addCon('con',upper=0, scale=1.0)
optProb.addObj('f')

# Create optimizer
opt = OPT()
if testHist == 'no':
    # Just run a normal run
    sol = opt(optProb, sens=sens, sensMode=sensMode)
    print(sol.fStar)
else:
    # First call just does 10 iterations
    snopt.setOption('Major iterations limit',10)
    solSnopt1 = snopt(optProb, sens=sens, sensMode='pgc', storeHistory='opt_hist')

    # Now we are allowed to do 50
    snopt.setOption('Major iterations limit',50)
    if testHist == 'hot':
        solSnopt2 = snopt(optProb, sens=sens, sensMode=sensMode,
                          hotStart='opt_hist', storeHistory='opt_hist')
    else:
        solSnopt2 = snopt(optProb, sens=sens, sensMode=sensMode,
                          coldStart='opt_hist', storeHistory='opt_hist')

    print(solSnopt2.fStar)
