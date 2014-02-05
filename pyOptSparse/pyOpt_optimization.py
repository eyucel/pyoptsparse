#!/usr/bin/env python
"""
pyOptSparse_optimization

Holds the Python Design Optimization Class

The main purpose, of this class is to describe the struture and
potentially, sparsity pattern of an optimization problem.

Copyright (c) 2013-2014 by Dr. Gaetan Kenway
All rights reserved.

Developers:
-----------
- Dr. Gaetan K.W. Kenway (GKK)

History
-------
    v. 1.0  - Initial Class Creation (GKK, 2013)
"""
from __future__ import print_function

# =============================================================================
# Standard Python modules
# =============================================================================
import os, sys, time, copy

try:
    from collections import OrderedDict
except ImportError:
    try:
        from ordereddict import OrderedDict
    except ImportError:
        print('Could not find any OrderedDict class. For 2.6 and earlier, \
use:\n pip install ordereddict')
    
# =============================================================================
# External Python modules
# =============================================================================
import numpy
# pylint: disable-msg=E0611
from scipy import sparse
from mpi4py import MPI
# =============================================================================
# Extension modules
# =============================================================================
from .pyOpt_variable import Variable
from .pyOpt_objective import Objective
from .pyOpt_constraint import Constraint
from .pyOpt_error import Error
# =============================================================================
# Misc Definitions
# =============================================================================
inf = 1e20  # define a value for infinity

# =============================================================================
# Optimization Class
# =============================================================================
class Optimization(object):
    """
    Create a description of an optimization probelem. 

    Parameters
    ----------
    name : str
        Name given to optimization problem. This is name is currently
        not used for anything, but may be in the future.

    objFun : python function
        Python function handle of function used to evaluate the objective
        function.

    useGroups : bool
        Flag to specify whether or not design variables are returned in a
        flattened array or as a dictionary. It is **highly** recommened that
        useGroups is **always** used, even for small problems.
        """
      
    def __init__(self, name, objFun, comm=None, useGroups=True):

        self.name = name
        self.objFun = objFun
        self.useGroups = useGroups
        if comm is None:
            self.comm = MPI.COMM_WORLD
        else:
            self.comm = comm
            
        # Ordered dictionaries to keep track of variables and constraints
        self.variables = OrderedDict()
        self.constraints = OrderedDict()
        self.objectives = OrderedDict()
        self.dvOffset =  OrderedDict()

        # Sets to keep track of user-supplied names --- Keep track of
        # varSets, varGroup and constraint names independencely
        self.varSetNames = set()
        self.varGroupNames = set()
        self.conGroupNames = set()

        # Flag to determine if adding variables is legal. 
        self.ableToAddVariables = True
        self.denseJacobianOK = True
        
        # Variables to be set in reorderConstraintJacobian after we
        # have finalized the specification of the variable and the
        # constraints
        self.ndvs = None
        self.conScaleNonLinear = None
        self.conScaleLinear = None
        self.nnCon = None
        self.nCon = None
        self.xscale = None
        self.nlCon = None
        self.linearJacobian = None
        self.dummyConstraint = False
        
    def addVarSet(self, name):
        """An outer grouping of design variables. These sets are used
        when specifiying the sparsity structure of the constraint
        jacobian
        """

        self.checkOkToAddVariables()

        if name in self.varSetNames:
            raise Error('The supplied name \'%s\' for a variable set \
            has already been used.'% name)

        self.varSetNames.add(name)
        self.variables[name] = OrderedDict()

    def addVar(self, name, *args, **kwargs):
        """
        This is a convience function. See addVarGroup for the
        appropriate list of parameters.
        """

        self.addVarGroup(name, 1, *args, scalar=True, **kwargs)

    def addVarGroup(self, name, nVars, type='c', value=0.0, 
                    lower=None, upper=None, scale=1.0, 
                    varSet=None, choices=None, **kwargs):
        """
        Add a group of variables into a variable set. This is the main
        function used for adding variables to pyOptSparse.

        Parameters
        ----------
        name : str
            Name of variable group. This name should be unique across all the design variable groups

        nVars : int
            Number of design variables in this group.

        type : str. 
            String representing the type of variable. Suitable values for type
            are: 'c' for continuous variables, 'i' for integer values and
            'd' for discrete selection. 

        value : scalar or array. 
            Starting value for design variables. If it is a a scalar, the same
            value is applied to all 'nVars' variables. Otherwise, it must be
            iterable object with length equal to 'nVars'.

        lower : scalar or array. 
            Lower bound of variables. Scalar/array usage is the same as value
            keyword

        upper : scalar or array. 
            Upper bound of variables. Scalar/array usage is the same as value
            keyword
            
        scale : scalar or array. 
            Define a user supplied scaling variable for the design variable group.
            This is often necessary when design variables of widely varraying magnitudes
            are used within the same optimization. Scalar/array usage is the same
            as value keyword.

        varSet : str. 
            Specify which variable set this design variable group
            belongs. If this is not specified, it will be added to a
            varSet whose name is the sanme as \'name\'. 
            
        choices : list
            Specify a list of choices for discrete design variables
            
        Examples
        --------
        >>> # Add a single design variable 'alpha' to the default variable set
        >>> optProb.addVar('alpha', type='c', value=2.0, lower=0.0, upper=10.0, \
        scale=0.1)
        >>> # Add a single variable to its own varSet (varSet is not needed)
        >>> optProb.addVar('alpha_c1', type='c', value=2.0, lower=0.0, upper=10.0, \
        scale=0.1)
        >>> # Add 10 unscaled variables of 0.5 between 0 and 1 to varSet 'y_vars'
        >>> optProb.addVarGroup('y', type='c', value=0.5, lower=0.0, upper=1.0, \
        scale=1.0, varSet='y_vars')
        >>> # Add another scaled variable to the varSet 'y_vars'
        >>> optProb.addVar('y2', type='c', value=0.25, lower=0.0, upper=1.0, \
        scale=.5, varSet='y_vars')
        
        Notes
        -----
        Calling addVar() and addVarGroup(..., nVars=1, ...) are
        **NOT** equilivant! The variable added with addVar() will be
        returned as scalar, while variable returned from addVarGroup
        will be an array of length 1.

        It is recommended that the addVar() and addVarGroup() calls
        follow the examples above by including all the keyword
        arguments. This make it very clear the itent of the script's
        author. The type, value, lower, upper and scale should be
        given for all variables even if the default value is used. 
        """

        self.checkOkToAddVariables()

        if name in self.varGroupNames:
            raise Error('The supplied name \'%s\' for a variable group \
has already been used.'% name)
        else:
            self.varGroupNames.add(name)

        if varSet is None:
            varSet = name
        if not varSet in self.variables:
            self.addVarSet(varSet)

        # Check that the type is ok:
        if type not in ['c', 'i', 'd']:
            raise Error('Type must be one of \'c\' for continuous, \
\'i\' for integer or \'d\' for discrete.')
                    
        # ------ Process the value arguement
        value = numpy.atleast_1d(value).real
        if len(value) == 1:
            value = value[0]*numpy.ones(nVars)
        elif len(value) == nVars:
            pass
        else:
            raise Error('The length of the \'value\' argument to \
 addVarGroup is %d, but the number of variables in nVars is %d.'% (
                    len(value), nVars))

        # ------ Process the lower bound argument
        if lower is None:
            lower = -inf*numpy.ones(nVars)
        else:
            lower = numpy.atleast_1d(lower).real
            if len(lower) == 1:
                lower = lower[0]*numpy.ones(nVars)
            elif len(lower) == nVars:
                pass
            else:
                raise Error('The length of the \'lower\' argument to \
addVarGroup is %d, but the number of variables in nVars is %d.'% (
                        len(lower), nVars))

        # ------ Process the upper bound argument
        if upper is None:
            upper = inf*numpy.ones(nVars)
        else:
            upper = numpy.atleast_1d(upper).real
            if len(upper) == 1:
                upper = upper[0]*numpy.ones(nVars)
            elif len(upper) == nVars:
                pass
            else:
                raise Error('The length of the \'upper\' argument to \
addVarGroup is %d, but the number of variables in nVars is %d.'% (
                        len(upper), nVars))

        # ------ Process the scale bound argument
        if scale is None:
            scale = numpy.ones(nVars)
        else:
            scale = numpy.atleast_1d(scale)
            if len(scale) == 1:
                scale = scale[0]*numpy.ones(nVars)
            elif len(scale) == nVars:
                pass
            else:
                raise Error('The length of the \'scale\' argument to \
addVarGroup is %d, but the number of variables in nVars is %d.'% (
                        len(scale), nVars))

        # Determine if scalar i.e. it was called from addVar():
        scalar = kwargs.pop('scalar', False)

        # Now create all the variable objects
        self.variables[varSet][name] = []
        for iVar in range(nVars):
            varName = name + '_%d'% iVar
            self.variables[varSet][name].append(
                Variable(varName, type=type, value=value[iVar],
                         lower=lower[iVar], upper=upper[iVar],
                         scale=scale[iVar], scalar=scalar, choices=choices))

    def delVar(self, name):
        """
        Delete a variable or variable group

        Parameters
        ----------
        name : str
           Name of variable or variable group to remove
           """
        deleted = False
        for dvSet in self.variables:
            for dvGroup in self.variables[dvSet]:
                if dvGroup == name:
                    self.variables[dvSet].pop(dvGroup)
                    deleted = True

        if not deleted:
            print('%s was not a valid design variable name'% name)
            
    def delVarSet(self, name):
        """
        Delete all variables belonging to a variable set

        Parameters
        ----------
        name : str
           Name of variable or variable group to remove
           """

        assert name in self.variables, '%s not a valid varSet.'% name
        self.variables.pop(name)

    def _reduceDict(self, variables):
        """
        This is a specialized function that is used to communicate
        variables from dictionaries across the comm to ensure that all
        processors end up with the same dictionary. It is used for
        communicating the design variables and constrainted, which may
        be specified on different processors independently.
        """
        
        # Step 1: Gather just the key names:
        allKeys = self.comm.gather(list(variables.keys()), root=0)
       
        # Step 2: Determine the unique set:
        procKeys = {}
        if self.comm.rank == 0:
            # We can do the reduction efficiently using a dictionary: The
            # algorithm is as follows: . Loop over the processors in order,
            # and check if key is in procKeys. If it isn't, add with proc
            # ID. This ensures that when we're done, the keys of 'procKeys'
            # contains all the unique values we need, AND it has a single
            # (lowest proc) that contains that key
            for iProc in range(len(allKeys)):
                for key in allKeys[iProc]:
                    if not key in procKeys:
                        procKeys[key] = iProc

            # Now pop any keys out with iProc = 0, since we want the
            # list of ones NOT one the root proc
            for key in list(procKeys.keys()):
                if procKeys[key] == 0:
                    procKeys.pop(key)

        # Step 3. Now broadcast this back to everyone
        procKeys = self.comm.bcast(procKeys, root=0)
     
        # Step 4. The required processors can send the variables
        if self.comm.rank == 0:
            for key in procKeys:
                variables[key] = self.comm.recv(source=procKeys[key], tag=0)
        else:
            for key in procKeys:
                if procKeys[key] == self.comm.rank:
                    self.comm.send(variables[key], dest=0, tag=0)

        # Step 5. And we finally broadcast the final list back:
        variables = self.comm.bcast(variables, root=0)

        return variables
    
    def finalizeDesignVariables(self):
        """
        This function **MUST** be called by all the processors in the
        communicator given to this function. The reason for this, is
        that we allow design variables to be added on any of the
        processors in this comm. This function is therefore collective
        and combines the variables added on each of processors to come
        up with a consistent set. Note that adding a variable group
        with the SAME name but DIFFERENT parameters is undefined and
        may result in very strange behaviour. 
         """

        # First thing we need is to determine the consistent set of
        # variables from all processors.
        self.variables = self._reduceDict(self.variables)

        dvCounter = 0
        for dvSet in self.variables:
            # Check that varSet *actually* has variables in it:
            if len(self.variables[dvSet]) > 0:
                self.dvOffset[dvSet] = OrderedDict()
                self.dvOffset[dvSet]['n'] = [dvCounter, -1]
                for dvGroup in self.variables[dvSet]:
                    n = len(self.variables[dvSet][dvGroup])
                    self.dvOffset[dvSet][dvGroup] = [
                        dvCounter, dvCounter + n, 
                        self.variables[dvSet][dvGroup][0].scalar]
                    dvCounter += n
                self.dvOffset[dvSet]['n'][1] = dvCounter
            else:
                # Get rid of the dvSet since it has no variable groups
                self.variables.pop(dvSet)

        self.ndvs = dvCounter
        self.ableToAddVariables = False

    def addObj(self, name, *args, **kwargs):
        """
        Add Objective into Objectives Set
        """
        
        self.objectives[name] = Objective(name, *args, **kwargs)

    def addCon(self, name, *args, **kwargs):
        """
        Convenience function. See addConGroup() for more information
        """
        
        self.addConGroup(name, 1, *args, **kwargs)

    def addConGroup(self, name, nCon, lower=None, upper=None, scale=1.0, 
                    linear=False, wrt=None, jac=None):
        """
        Add a group of variables into a variable set. This is the main
        function used for adding variables to pyOptSparse.

        Parameters
        ----------
        name : str
            Constraint name. All names given to constraints must be unique

        nCon : int
            The number of constraints in this group

        lower : scalar or array
            The lower bound(s) for the constraint. If it is a scalar, 
            it is applied to all nCon constraints. If it is an array, 
            the array must be the same length as nCon.

        upper : scalar or array
            The upper bound(s) for the constraint. If it is a scalar, 
            it is applied to all nCon constraints. If it is an array, 
            the array must be the same length as nCon.

        scale : scalar or array

            A scaling factor for the constraint. It is generally
            advisible to have most optimization constraint around the
            same order of magnitude.

        linear : bool
            Flag to specifiy if this constraint is linear. If the
            constraint is linear, both the 'wrt' and 'jac' keyword
            arguments must be given to specify the constant portion of
            the constraint jacobian.

        wrt : iterable (list, set, OrderedDict, array etc)
            'wrt' stand for stands for 'With Respect To'. This
            specifies for what dvSets have non-zero jacobian values
            for this set of constraints. The order is not important.

        jac : dictionary
            For linear and sparse non-linear constraints, the constraint
            jacobian must be passed in. The structure is jac dictionary
            is as follows:

            {'dvSet1':<matrix1>, 'dvSet2', <matrix1>}

            They keys of the jacobian must correpsond to the dvSets
            givn in the wrt keyword argument. The dimensions of each
            "chunk" of the constraint jacobian must be consistent. For
            example, <matrix1> must have a shape of (nCon, nDvs) where
            nDVs is the **total** number of all design variables in
            dvSet1. <matrix1> may be a desnse numpy array or it may be
            scipy sparse matrix. It each case, the matrix shape must
            be as previously described. 

            Note that for nonlinear constraints (linear=False), the
            values themselves in the matrices in jac do not matter, 
            but the sparsity structure **does** matter. It is
            imparative that entries that will at some point have
            non-zero entries have non-zero entries in jac
            argument. That is, we do not let the sparsity structure of
            the jacobian change throughout the optimization. This
            stipulation is automatically checked internally. 
            """

        # If this is the first constraint, finalize the variables to
        # ensure no more variables can be added. 
        if self.ableToAddVariables:
            raise Error('The user MUST call finalizeDesignVariables on all\
            processors of the optProb communicator before constraints can \
            be added.')

        if name in self.conGroupNames:
            raise Error('The supplied name \'%s\' for a constraint group \
has already been used.'% name)

        self.conGroupNames.add(name)

        # ------ Process the lower bound argument
        if lower is None:
            lower = -inf*numpy.ones(nCon)
        else:
            lower = numpy.atleast_1d(lower)
            if len(lower) == 1:
                lower = lower[0]*numpy.ones(nCon)
            elif len(lower) == nCon:
                pass
            else:
                raise Error('The length of the \'lower\' argument to \
addConGroup is %d, but the number of constraints is %d.'% (
                        len(lower), nCon))

        # ------ Process the upper bound argument
        if upper is None:
            upper = inf*numpy.ones(nCon)
        else:
            upper = numpy.atleast_1d(upper)
            if len(upper) == 1:
                upper = upper[0]*numpy.ones(nCon)
            elif len(upper) == nCon:
                pass
            else:
                raise Error('The length of the \'upper\' argument to \
addConGroup is %d, but the number of constraints is %d.'%(
                        len(upper), nCon))

        # ------ Process the scale argument
        scale = numpy.atleast_1d(scale)
        if len(scale) == 1:
            scale = scale[0]*numpy.ones(nCon)
        elif len(scale) == nCon:
            pass
        else:
            raise Error('The length of the \'scale\' argument to \
 addConGroup is %d, but the number of constraints is %d.'%(
                    len(scale), nCon))
        
        # First check if 'wrt' is supplied...if not we just take all
        # the dvSet
        if wrt is None:
            wrt = list(self.variables.keys())
        else:
            # Sanitize the wrt input:
            if isinstance(wrt, str):
                wrt = [wrt.lower()]
            else: 
                try:
                    wrt = list(wrt)
                except:
                    raise Error('\'wrt\' must be a iterable list')

            # We allow 'None' to be in the list...they are null so
            # just pop them out:
            wrt = [dvSet for dvSet in wrt if dvSet != None]
                    
            # Now, make sure that each dvSet the user supplied list
            # *actually* are DVsets
            for dvSet in wrt:
                if not dvSet in self.variables:
                    raise Error('The supplied dvSet \'%s\' in \'wrt\' \
for the %s constraint, does not exist. It must be added with a call to \
addVar() or addVarGroup() with a dvSet=\'%s\' keyword argument.'% (
                            dvSet, name, dvSet))

        # Last thing for wrt is to reorder them such that dvsets are
        # in order. This way when the jacobian is assembled in
        # processDerivatives() the coorindate matrix will in the right
        # order.
        dvStart = []
        for dvSet in wrt:
            dvStart.append(self.dvOffset[dvSet]['n'][0])

        # This sort wrt using the keys in dvOffset
        wrt = [x for (y, x) in sorted(zip(dvStart, wrt))]

        # Now we know which DVsets this constraint will have a
        # derivative with respect to (i.e. what is in the wrt list)
            
        # Now, it is possible that jacobians were given for none, some
        # or all the dvSets defined in wrt. 
        if jac is None:

            # If the constraint is linear we have to *Force* the user to
            # supply a constraint jacobian for *each* of the values in
            # wrt. Otherwise, a matrix of zeros isn't meaningful for the
            # sparse constraints.

            if linear:
                raise Error('The \'jac\' keyword argument to addConGroup()\
                must be supplied for a linear constraint')

            # without any additional information about the jacobian
            # structure, we must assume they are all dense. 
            jac = {}
            for dvSet in wrt:
                ss = self.dvOffset[dvSet]['n']                 
                ndvs = ss[1]-ss[0]
                jac[dvSet] = sparse.csr_matrix(numpy.ones((nCon, ndvs)))
                jac[dvSet].data[:] = 0.0
                
            # Set a flag for the constraint object, that not returning
            # them all is ok.
            partialReturnOk = True

        else:
            # First sanitize input:
            if not isinstance(jac, dict):
                raise Error('The \'jac\' keyword argument to \
                addConGroup() must be a dictionary')

            # Now loop over the set we *know* we need and see if any
            # are in jac. We will actually pop them out, and that way
            # if there is anything left at the end, we can tell the
            # user supplied information was unused. 
            tmp = copy.deepcopy(jac)
            jac = {}
            for dvSet in wrt:
                ss = self.dvOffset[dvSet]['n']                 
                ndvs = ss[1]-ss[0]

                try:
                    jac[dvSet] = tmp.pop(dvSet)
                    # Check that this user-supplied jacobian is in
                    # fact the right size
                except:
                    # No big deal, just make a dense component...and
                    # set to zero
                    jac[dvSet] = sparse.csr_matrix(numpy.ones((nCon, ndvs)))
                    jac[dvSet].data[:] = 0.0
                    
                if jac[dvSet].shape[0] != nCon or jac[dvSet].shape[1] != ndvs:
                    raise Error('The supplied jacobian for dvSet \'%s\'\
 in constraint %s, was the incorrect size. Expecting a jacobian\
 of size (%d, %d) but received a jacobian of size (%d, %d).'%(
                            dvSet, name, nCon, ndvs, jac[dvSet].shape[0], 
                            jac[dvSet].shape[1]))

                # Now check that the supplied jacobian is sparse of not:
                if sparse.issparse(jac[dvSet]):
                    # Excellent, the user supplied a sparse matrix or
                    # we just created one above. Convert to csr format
                    # if not already in that format.
                    jac[dvSet] = jac[dvSet].tocsr()
                else:
                    # Supplied jacobian is dense, replace any zero, 
                    # before converting to csr format
                    jac[dvSet][numpy.where(jac[dvSet]==0)] = 1e-50
                    jac[dvSet] = sparse.csr_matrix(jac[dvSet])
            # end for (dvSet)

            # If there is anything left in jac print a warning:
            for dvSet in tmp:
                print('pyOptSparse Warning: An unused jacobian with \
dvSet key of \'%s\' was unused. This will be ignored'% dvSet)

            # Finally partial returns NOT ok, since the user has
            # supplied information about the sparsity:
            partialReturnOk = False

        # end if (if Jac)
        
        # Scale the rows of each jacobian part:
        for dvSet in jac:
            self._csrRowScale(jac[dvSet], scale)

        # Finally! Create constraint object
        self.constraints[name] = Constraint(
            name, linear, wrt, jac, partialReturnOk,
            lower*scale, upper*scale, scale)

    def getDVs(self):
        """
        Return a dictionary of the design variables
        """ 
        if self.useGroups:
            outDVs = {}
            for dvSet in self.variables:
                outDVs[dvSet] = {}
                for dvGroup in self.variables[dvSet]:
                    temp = []
                    for var in self.variables[dvSet][dvGroup]:
                        temp.append(var.value)
                    outDVs[dvSet][dvGroup] = numpy.array(temp)
        else:
            outDVs = numpy.zeros(self.ndvs)
            for dvSet in self.variables:
                for dvGroup in self.variables[dvSet]:
                    istart = self.dvOffset[dvSet][dvGroup][0]
                    iend   = self.dvOffset[dvSet][dvGroup][1]
                    scalar = self.dvOffset[dvSet][dvGroup][2]
                    if scalar:
                        outDVs[istart] = self.variables[dvSet][dvGroup][0].value
                    else:
                        for i in range(istart, iend):
                            outDVs[i] = self.variables[dvSet][dvGroup][i].value

        return outDVs

    def setDVs(self, inDVs):
        """
        set the problem design variables from a dictionary. Set only the
        values that are in the dictionary. add in some type checking as well
        """

        if self.useGroups:
            for dvSet in set(inDVs.keys()) & set(self.variables.keys()):
                for dvGroup in set(inDVs[dvSet])&set(self.variables[dvSet]):
                    groupLength = len(dvGroup)
                    for i in range(groupLength):
                        self.variables[dvSet][dvGroup][i].value = inDVs[dvSet][dvGroup][i]

        else:
            for dvSet in list(self.variables.keys()):
                for dvGroup in self.variables[dvSet]:
                    istart = self.dvOffset[dvSet][dvGroup][0]
                    iend   = self.dvOffset[dvSet][dvGroup][1]
                    scalar = self.dvOffset[dvSet][dvGroup][2]
                    if scalar:
                        self.variables[dvSet][dvGroup][0].value = inDVs[istart]
                    else:
                        for i in range(istart, iend):
                            self.variables[dvSet][dvGroup][i].value = inDVs[i]
    def printSparsity(self):
        """
        This function prints an (ascii) visualization of the jacobian
        sparsity structure. This helps the user visualize what
        pyOptSparse has been given and helps ensure it is what the
        user expected. It is highly recommended this function be
        called before the start of every optimization to verify the
        optimization problem setup.
        """

        if self.comm.rank != 0:
            return
    
        # Header describing what we are printing:
        print('+'+'-'*78+'-'+'+')
        print('|' + ' '*19 +'Sparsity structure of constraint Jacobian' + ' '*19 + '|')
        print('+'+'-'*78+'-'+'+')

        # We will do this with a 2d numpy array of characters since it
        # will make slicing easier

        # First determine the requried number of rows 
        nRow = 1 # Header
        nRow += 1 # Line
        maxConNameLen = 0
        hasLinear = False
        for iCon in self.constraints:
            nRow += 1 # Name
            con = self.constraints[iCon]
            maxConNameLen = max(maxConNameLen,
                                len(con.name)+3+int(numpy.log10(con.ncon))+1)
            nRow += 1 # Line
            if self.constraints[iCon].linear:
                hasLinear = True
        if hasLinear:
            nRow += 1 # Extra line to separate linear constraints

        # And now the columns:
        nCol = maxConNameLen
        nCol += 2 # Space plus line
        varCenters = []
        for iVar in self.variables:
            nvar = self.dvOffset[iVar]['n'] [1] - self.dvOffset[iVar]['n'][0]
            var_str = iVar + ' (%d)'% nvar

            varCenters.append(nCol + len(var_str)/2 + 1)
            nCol += len(var_str)
            nCol += 2 # Spaces on either side
            nCol += 1 # Line 

        txt = numpy.zeros((nRow, nCol), dtype=str)
        txt[:, :] = ' '
        # Outline of the matrix on left and top
        txt[1, maxConNameLen+1:-1] = '-'
        txt[2:-1, maxConNameLen+1] = '|'
     
        # Print the variable names:
        iCol = maxConNameLen + 2
        for iVar in self.variables:
            nvar = self.dvOffset[iVar]['n'] [1] - self.dvOffset[iVar]['n'][0]
            var_str = iVar + ' (%d)'% nvar
            l = len(var_str)
            txt[0, iCol+1 :iCol + l+1] = list(var_str)
            txt[2:-1, iCol + l + 2] = '|'
            iCol += l + 3

        # Print the constraint names;
        iRow = 2

        # Do the nonlinear ones first:
        for iCon in self.constraints:
            con = self.constraints[iCon]
            if not con.linear:
                name = con.name + ' (%d)'% con.ncon
                l = len(name)
                # The name
                txt[iRow, maxConNameLen-l:maxConNameLen] = list(name)

                # Now we write a 'D' for dense, 'S' for sparse or nothing. 
                varKeys = list(self.variables.keys())
                for iVar in range(len(varKeys)):
                    if varKeys[iVar] in con.wrt:
                        txt[iRow, varCenters[iVar]] = 'X'

                # The separator
                txt[iRow+1, maxConNameLen+1:] = '-'
                iRow += 2
            
        # Print an extra '---' to distinguish:
        if hasLinear:
            txt[iRow, maxConNameLen+1:] = '-'
            iRow += 1

        # Do the nonlinear ones first and then the linear ones:
        for iCon in self.constraints:
            con = self.constraints[iCon]
            if con.linear:
                name = con.name + ' (%d)'% con.ncon
                l = len(name)
                # The name
                txt[iRow, maxConNameLen-l:maxConNameLen] = list(name)

                # Now we write a 'D' for dense, 'S' for sparse or nothing. 
                varKeys = list(self.variables.keys())
                for iVar in range(len(varKeys)):
                    if varKeys[iVar] in con.wrt:
                        txt[iRow, varCenters[iVar]] = 'X'

                # The separator
                txt[iRow+1, maxConNameLen+1:] = '-'
                iRow += 2

        # Corners - just to make it nice :-)
        txt[1, maxConNameLen+1] = '+'
        txt[-1, maxConNameLen+1] = '+'
        txt[1, -1] = '+'
        txt[-1, -1] = '+'
        for i in range(len(txt)):
            print(''.join(txt[i]))

#=======================================================================
#       All the functions from here down should not need to be called
#       by the user. Most functions are public since the individual
#       optimizers need to be able to call them
#=======================================================================

    def checkOkToAddVariables(self):
        """
        ** This function should not need to be called by the user**
        Internal check if it is safe to add more variables"""
        
        if not self.ableToAddVariables:
            raise Error('No more variables can be added at this time. \
All variables must be added before constraints can be added.')
    
        
    def finalizeConstraints(self):
        """
        ** This function should not need to be called by the user**

        There are several functions for this routine:

        1. Reorder the supplied constraints such that all the
           nonlinear and linear constraints are grouped together.  By
           always reordering in this manner, if an optimizer doesn't
           support linear constraints explictly, we can just tack them
           on at the end of the non-linear jacobian

        2. Determine the final scaling array for the design variables

        3. Determine if it is possible to return a complete dense
           jacobian. Most of this time, we should be using the dictionary-
           based return

        4. Assemble the final (fixed) LINEAR constraint jacobian if it
           exists.
        """

        # First thing we need is to determine the consistent set of
        # constraints from all processors
        self.constraints = self._reduceDict(self.constraints)

        # ---------
        # Step 1.
        # ---------
        
        # Determine the total number of linear and nonlinear constraints:
        nlcon = 0 # Linear 
        nncon = 0 # nonlinear

        for iCon in self.constraints:
            if self.constraints[iCon].linear:
                nlcon += self.constraints[iCon].ncon
            else:
                nncon += self.constraints[iCon].ncon
        
        # Store number of linear and nonlinear constriants:
        self.nnCon = nncon
        self.nlCon = nlcon
        self.nCon = nncon + nlcon

        conScaleNonLinear = []
        conScaleLinear = []
        # Loop over the constraints assigning the row start (rs) and
        # row end (re) values. The actual ordering depends on if
        # constraints are reordered or not.
        rowCounter = 0 
        for iCon in self.constraints:
            con = self.constraints[iCon]
            if not con.linear:
                con.rs = rowCounter
                rowCounter += con.ncon
                con.re = rowCounter
                conScaleNonLinear.extend(con.scale)

        for iCon in self.constraints:
            con = self.constraints[iCon]
            if con.linear:
                con.rs = rowCounter
                rowCounter += con.ncon
                con.re = rowCounter
                conScaleLinear.extend(con.scale)

        # Save constraint scaling arrays
        self.conScaleNonLinear = numpy.array(conScaleNonLinear)
        self.conScaleLinear = numpy.array(conScaleLinear)

        # ---------
        # Step 2.
        # ---------
        
        # Also assemble the design variable scaling
        xscale = []
        for dvSet in self.variables:
            for dvGroup in self.variables[dvSet]:
                for var in self.variables[dvSet][dvGroup]:
                    xscale.append(var.scale)
        self.xscale = numpy.array(xscale)

        # ---------
        # Step 3.
        # ---------

        # We also can determine if it is possible to do a dense
        # return. We ONLY allow a dense return if no 'wrt' flags were
        # set:
        allVarSets = set(self.variables.keys())
        for iCon in self.constraints:
            con = self.constraints[iCon]
            # All entries of con.wrt in allVarSets
            if not set(con.wrt) <= allVarSets: 
                # If any constrant 'wrt' is not fully in allVarSets we
                # can't do a dense return
                self.denseJacobianOK = False

        # ---------
        # Step 4.
        # ---------

        # Assemble the linear constraint jacobian if self.nlcon > 0:
        if self.nlCon > 0:
            gcon = {}
            for iCon in self.constraints:
                if self.constraints[iCon].linear:
                    gcon[iCon] = self.constraints[iCon].jac

            # Now process them and save:
            self.linearJacobian = self.processConstraintJacobian(
                gcon, linearFlag=True)

    def processX(self, x):
        """
        ** This function should not need to be called by the user**

        Take the flattened array of variables in 'x' and return a
        dictionary of variables keyed on the name of each variable
        group if useGroups is True. 

        Parameters
        ----------
        x : array
            Flattened array from optimizer
        """

        if self.useGroups:
            xg = {}
            for dvSet in self.variables:
                for dvGroup in self.variables[dvSet]:
                    istart = self.dvOffset[dvSet][dvGroup][0]
                    iend   = self.dvOffset[dvSet][dvGroup][1]
                    scalar = self.dvOffset[dvSet][dvGroup][2]
                    if scalar:
                        xg[dvGroup] = x[istart]
                    else:
                        xg[dvGroup] = x[istart:iend].copy()
                   
            return xg
        else:
            return x

    def deProcessX(self, x):
        """
        ** This function should not need to be called by the user**

        Take the dictionary form of x and convert back to flattened
        array. Nothing is done if useGroups is False (since we should
        not have a dictionary in that case)

        Parameters
        ----------
        x : dict
            Dictionary form of variables

        Returns
        -------
        x_array : array
            Flattened array of variables
        """
        if self.useGroups:
            x_array = numpy.zeros(self.ndvs)
            for dvSet in self.variables:
                for dvGroup in self.variables[dvSet]:
                    istart = self.dvOffset[dvSet][dvGroup][0]
                    iend   = self.dvOffset[dvSet][dvGroup][1]
                    scalar = self.dvOffset[dvSet][dvGroup][2]
                    if scalar:
                        x_array[istart] = x[dvGroup]
                    else:
                        x_array[istart:iend] = x[dvGroup]
            return x_array
        else:
            return x

    def processObjective(self, fobj_in, obj='f'):
        """
        ** This function should not need to be called by the user**

        This is currently just a stub-function. It is here since it
        the future we may have to deal with multiple objectives so
        this function will deal with that

        Parameters
        ----------
        obj_in : float or dict
            Single objective is a float or a dict if multiple ones

        obj : str
            The name of the objective to process

        Returns
        -------
        obj : float or array
            Processed objective.
            """

        # Just scale the objective 
        fobj = fobj_in * self.objectives[obj].scale

        return fobj

    def processNonlinearConstraints(self, fcon_in, scaled=True, dtype='d'):
        """
        ** This function should not need to be called by the user**

        Parameters
        ----------
        fcon_in : array or dict
            Array of constraint values or a dictionary of constraint
            values

        scaled : bool
            Flag specifying if the returned array should be scaled by
            the pyOpt scaling.
            """

        # We will actually be a little leniant here; the user CAN
        # return an iterable of the correct length and we will accept
        # that. Otherwise we will use the dictionary formulation

        if self.dummyConstraint:
            return numpy.array([0])

        if scaled:
            scaleFact = self.conScaleNonLinear
        else:
            scaleFact = numpy.ones_like(self.conScaleNonLinear)
        
        # We REQUIRE that fcon_in is a dict:
        fcon = numpy.zeros(self.nnCon, dtype=dtype)
        for iCon in self.constraints:
            con = self.constraints[iCon]
            if not con.linear:
                if iCon in fcon_in:
                    
                    # Make sure it is at least 1dimension:
                    c = numpy.atleast_1d(fcon_in[iCon])
                        
                    # Make sure it is the correct size:
                    if len(c) == self.constraints[iCon].ncon:
                        fcon[con.rs:con.re] = c
                    else:
                        raise Error('%d constraint values were returned in\
 %s, but expected %d.'%(len(fcon_in[iCon]), iCon, self.constraints[iCon].ncon))
                else:
                    raise Error('No constraint values were found for the \
constraint \'%s\'.'%(iCon))

        # Finally convert to array, scale and return:
        return  scaleFact*fcon

    def evaluateLinearConstraints(self, x):
        """
        This function is required for optimizers that do not explictly
        treat the linear constraints. For those optimizers, we will
        evaluate the linear constraints here, such that they can be
        appended to the nonlinear constraints. Note that we have
        reordered the constraints so such that the nonlinear
        constraints come first.

        Parameters
        ----------
        x : array
            This must be the unprocessed x-vector from the optimizer
            """

        # This is actually pretty easy; it's just a matvec
        if self.linearJacobian is not None:
            linearConstraints = self.linearJacobian.dot(x)
            return linearConstraints
        else:
            return []
            # raise Error('For some reason the evaluateLinearConstraints()\
            # function was called but no linear constraints are defined \
            # for this optimization problem. This is a bug.')

    def processObjectiveGradient(self, gobj_in, obj='f'):
        """
        ** This function should not need to be called by the user**
        
        This generic function is used to assemble the objective
        gradient 

        Parameters
        ----------
        obj : str
            The name of the objective to process
        
        gobj_in : array or dict
            Objective gradient. Either a complete array or a
            dictionary of gradients given with respect to the
            dvSets. It is HIGHLY recommend to use the dictionary-based
            return. 
        """

        dvSets = list(self.variables.keys())
        
        if isinstance(gobj_in, dict):
            gobj = numpy.zeros(self.ndvs)

            # We loop over the keys provided in gob_in, instead of the
            # all the actual dvSet keys since dvSet keys not in
            # gobj_in will just be left to zero. We have implictly
            # assumed that the objective gradient is dense and any
            # keys that are provided are simply zero. 
            
            for key in gobj_in:
                # Check that the key matches something in dvSets
                if key in dvSets:
                    # Now check that the array is the correct length:

                    # ss = start/stop is a length 2 array of the
                    # indices for dvSet given by key
                    ss = self.dvOffset[key]['n'] 
                    if len(gobj_in[key]) == ss[1]-ss[0]:
                        # Everything checks out so set:
                        gobj[ss[0]:ss[1]] = numpy.array(gobj_in[key])
                    else:
                        raise Error('The length of the objective deritative \
for dvSet %s is the incorrect length. Expecting a length of %d but received \
a length of %d.'% (key, ss[1]-ss[0], len(gobj_in[key])))
                else:
                    print('Warning: The key \'%s\' in g_obj does not \
                    match any of the added DVsets. This derivative \
                    will be ignored'%(key))

        else:
            # Otherwise we will assume it is a vector:
            gobj = numpy.atleast_1d(gobj_in).copy()
            if len(gobj) != self.ndvs:
                raise Error('The length of the objective derivative for all \
design variables is not the correct size. Received size %d, should be \
size %d.'%(len(gobj), self.ndvs))
                
        # Finally scale the objective gradient based on the scaling
        # data for the design variables
        gobj /= self.xscale

        # Also apply the objective scaling
        gobj *= self.objectives[obj].scale

        return gobj

    def processConstraintJacobian(self, gcon, linearFlag=False):
        """
        ** This function should not need to be called by the user**
        
        This generic function is used to assemble the nonlinear
        constraint jacobian.  Also note that this function performs
        the pyOpt controlled scaling that is transparent to the user.

        Parameters
        ----------
        gcon_in : array or dict
            Constraint gradients. Either a complete 2D array or a nested
            dictionary of gradients given with respect to the dvSets

        linearFlag : bool
            Flag denoting which part of the constraint jacobian to
            form. If linear is True, we get the linear part, otherwise
            the non-linear part. Notet that function should NOT be
            called with linear=True if there are no no-linear
            constraints.

        Returns
        -------
        gcon : scipy.sparse.coo_matrix
            Return the jacobain in a sparse coo-rdinate matrix. This
            can be easily converted to csc, csr or dense format as
            required by individual optimizers
            """

        if self.nCon == 0:
            # We don't have constraints at all! However we *may* have to
            # include a dummy constraint:

            if self.dummyConstraint:
                return sparse.coo_matrix(1e-50*numpy.ones((1, self.ndvs)))
            else:
                return numpy.array([], 'd')

        # If the user has supplied a complete dense numpy array for
        # the jacobain AND all the constriants are dense
        if not isinstance(gcon, dict):
            try:
                gcon = numpy.atleast_2d(numpy.array(gcon).copy())
            except:
                pass

        # Determine the final shape of this part of the jacobian 
        if linearFlag:
            shp = (self.nlCon, self.ndvs)
            conScale = self.conScaleLinear
        else:
            shp = (self.nnCon, self.ndvs)
            conScale = self.conScaleNonLinear
            
        # Full dense return:
        if isinstance(gcon, numpy.ndarray):
            # Shape matches:
            if gcon.shape == shp:
                # Check that we are actually allowed a dense return:
                if self.denseJacobianOK:
                    # Replce any zero entries with a small value
                    gcon[numpy.where(gcon==0)] = 1e-50

                    # Do columing scaling (dv scaling)
                    for i in range(self.ndvs):
                        gcon[:, i] /= self.xscale[i]

                    # Now make it sparse
                    gcon = sparse.coo_matrix(gcon)

                    # Do the row scaling (constraint scaling)
                    self._cooRowScale(gcon, conScale)

                    # We are done so return:
                    return gcon
                else:
                    raise Error('A dense return is NOT possible since the user \
                    has specified the \'wrt\' flag for some constraint entries. \
                    You must use either the dictionary return OR add all \
                    constraint groups without the \'wrt\' flag')
            else:
                raise Error('The dense jacobian return was the incorrect size.\
 Expecting size of (%d, %d) but received size of (%d, %d).'% (
                    shp[0], shp[1],  gcon.shape[0], gcon.shape[1]))
        # end if (array return)
        
        # We now know we must process as a dictionary. Below are the
        # lists for the matrix entris. 
        data = []
        row  = []
        col  = []

        # Otherwise, process constraints in the dictionary form. 
        # Loop over all constraints:
        for iCon in self.constraints:
            con = self.constraints[iCon]
            if con.linear == linearFlag:
                if not con.name in gcon:
                    raise Error('The jacobian for the constraint \'%s\' was \
not found in the returned dictionary.'% con.name)

                if not con.partialReturnOk:
                    # The keys in gcon[iCon] MUST match PRECISELY
                    # the keys in con.wrt....The user told us they
                    # would supply derivatives wrt to these sets, and
                    # then didn't, so scold them. 
                    for dvSet in con.jac:
                        if dvSet not in gcon[iCon]:
                            raise Error('Constraint \'%s\' was expecting\
 a jacobain with respect to dvSet \'%s\' as was supplied in addConGroup(). \
This was not found in the constraint jacobian dictionary'% (con.name, dvSet))

                # Now loop over all required keys for this constraint:
                for key in con.wrt:

                    ss = self.dvOffset[key]['n'] 
                    ndvs = ss[1]-ss[0]

                    if key in gcon[iCon]:
                        # The key is actually returned:
                        
                        if sparse.issparse(gcon[iCon][key]):
                            # Excellent, the user supplied a sparse matrix
                            # Convert to csr format if not already in that
                            # format.
                            tmp = gcon[iCon][key].copy().tocsr()
                        else:
                            # Supplied jacobian is dense, replace any zero, 
                            # before converting to csr format
                            tmp = numpy.atleast_2d(gcon[iCon][key])
                            tmp[numpy.where(tmp==0)] = 1e-50
                            tmp = sparse.csr_matrix(tmp.copy())
                    else:
                        # This key is not returned. Just use the
                        # stored jacobian that contains zeros
                        tmp = con.jac[key]

                    # Now check that the jacobian is the correct shape
                    if not(tmp.shape[0] == con.ncon and tmp.shape[1] == ndvs):
                        raise Error('The shape of the supplied constraint \
jacobian for constraint %s is incorrect. Expected an array of shape (%d, %d), \
but received an array of shape (%d, %d).'% (con.name, con.ncon, ndvs, 
                                            tmp.shape[0], tmp.shape[1]))

                    # Now check that the csr matrix has the correct
                    # number of non zeros:
                    if tmp.nnz != con.jac[key].nnz:
                        raise Error('The number of nonzero elements for \
  constraint group \'%s\' was not the correct size. The supplied jacobian has \
 %d nonzero entries, but must contain %d nonzero entries.'%(con.name, tmp.nnz, 
                                                            con.jac[key].nnz))

                    # Loop over the number of row in this constraint
                    # jacobain group:
                    for iRow in range(con.ncon):

                        # Loop over the number of nonzero entries in this row:
                        for ii in range(con.jac[key].indptr[iRow],
                                        con.jac[key].indptr[iRow+1]):
                            row.append(con.rs + iRow)
                            icol = self.dvOffset[key]['n'][0] + \
                                   con.jac[key].indices[ii]
                            col.append(icol)

                            # The next line performs the column (dv scaling)
                            data.append(tmp.data[ii]/self.xscale[icol])
                            
                        # end for loop over local columns
                    # end for local loop over constraint keys
                # end for (key in constraint)
            # end if constraint nonlinear
        # end for constraint loop

        row = numpy.array(row, 'intc')
        col = numpy.array(col, 'intc')
        if linearFlag:
            row -= self.nnCon
            
        # Finally, the coo matrix and scale the rows (constraint scaling)
        gcon = sparse.coo_matrix((data, (row, col)), shp)
        self._cooRowScale(gcon, conScale)

        # Extract indices, and multiply by -1 and hstack
        #gcon = sparse.hstack((gcon, gcon[self.indices, :]*-1))

        return gcon

    def _csrRowScale(self, mat, vec):
        """
        Scale rows in csr matrix. Amazingly enough this is effectively
        impossible with scipy.sparse if you want to keep the nonzero
        structure. So we will brute force it here.
        """
        assert mat.shape[0] == len(vec)
        for iRow in range(mat.shape[0]):
            mat.data[mat.indptr[iRow]:mat.indptr[iRow+1]] *= vec[iRow]

    def _cooRowScale(self, mat, vec):
        """ 
        Scale rows of coo matrx. See _csrRowScale for why
        """
        assert mat.shape[0] == len(vec)
        for i in range(len(mat.data)):
            mat.data[i] *= vec[mat.row[i]]

    def __str__(self):
        """
        Print Structured Optimization Problem
        """
        
        text = """\nOptimization Problem -- %s\n%s\n
        Objective Function: %s\n\n    Objectives:
        Name        Value        Optimum\n""" % (
        self.name, '='*80, self.objFun.__name__)

        for obj in self.objectives:
            lines = str(self.objectives[obj]).split('\n')
            text += lines[1] + '\n'

        text += """\n	Variables (c - continuous, i - integer, d - discrete):
        Name    Type       Value       Lower Bound  Upper Bound\n"""

        for dvSet in self.variables:
            for dvGroup in self.variables[dvSet]:
                for var in self.variables[dvSet][dvGroup]:
                    lines = str(var).split('\n')
                    text += lines[1] + '\n'

        print('	    Name        Type'+' '*25+'Bound\n'+'	 ')
        if len(self.constraints) > 0:
            text += """\n	Constraints (i - inequality, e - equality):
        Name    Type                    Bounds\n"""
            for iCon in self.constraints:
                text += str(self.constraints[iCon])
        
        return text
 
#==============================================================================
# Optimization Test
#==============================================================================
if __name__ == '__main__':
    
    print('Testing Optimization...')
    optprob = Optimization('Optimization Problem', {})
    
    
