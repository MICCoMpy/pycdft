import numpy as np
import os
import shutil
from copy import deepcopy
import scipy.optimize
from pycdft.common import Sample
from pycdft.constraint import Constraint
from pycdft.dft_driver import DFTDriver


class CDFTSCFConverged(Exception):
    pass


class CDFTSolver:
    """ Constrained DFT solver.

    Attributes:
        job (str): "scf" or "opt".
        sample (Sample): the whole system for which CDFT calculation is performed.
        constraints (list of Constraint): constraints on the system.
        dft_driver (DFTDriver): the interface to DFT code (e.g. Qbox or PW).
        maxcscf (int): maximum number of CDFT iterations.
        maxstep (int): maximum geometry optimization steps.
        F_tol (float): force threshold for optimization.
        Vc_tot (float array, shape == [nspin, n1, n2, n3]): total constraint potential
            as a sum of all constraints defined on all fragments.
    """

    nsolver = 0

    def __init__(self, job: str, sample: Sample, dft_driver: DFTDriver,
                 optimizer: str="secant", maxcscf: int=1000, maxstep: int=100,
                 F_tol: float=1.0E-2):

        self.job = job
        self.sample = sample
        self.constraints = self.sample.constraints
        self.dft_driver = dft_driver
        self.optimizer = optimizer
        self.maxcscf = maxcscf
        self.maxstep = maxstep
        self.F_tol = F_tol
        self.Vc_tot = None
        self.itscf = None

        CDFTSolver.nsolver += 1
        self.isolver = CDFTSolver.nsolver
        self.output_path = "./pycdft_outputs/solver{}/".format(self.isolver)
        if os.path.exists(self.output_path):
            shutil.rmtree(self.output_path)
            os.makedirs(self.output_path)
        else:
            os.makedirs(self.output_path)
        self.dft_driver.reset(self.output_path)

    def solve(self):
        """ Solve CDFT SCF or optimization problem."""
        self.dft_driver.get_rho_r()
        if self.job == "scf":
            self.solve_scf()
        elif self.job == "opt":
            self.solve_opt()
        else:
            raise ValueError
        self.dft_driver.get_wfc()

    def solve_scf(self):
        """ Iteratively solve the CDFT problem.

        An outer loop (implemented below) is performed to maximize the free energy w.r.t.
        Lagrangian multipliers for all constrains, an inner loop (casted to a KS problem and
        outsourced to DFT code) is performed to minimize the free energy w.r.t. charge density.
        """

        self.sample.update_weights()

        if self.optimizer in ["secant", "bisect", "brentq", "brenth"]:
            assert len(self.constraints) == 1

        self.itscf = 0
        try:
            if self.optimizer == "secant":
                self.constraints[0].V_init = scipy.optimize.newton(
                    func=self.solve_scf_for_dW_by_dV,
                    x0=self.constraints[0].V_init,
                    maxiter=self.maxcscf
                )
            elif self.optimizer == "bisect":
                self.constraints[0].V_init, info = scipy.optimize.bisect(
                    f=self.solve_scf_for_dW_by_dV,
                    a=self.constraints[0].V_brak[0],
                    b=self.constraints[0].V_brak[1],
                    maxiter=self.maxcscf
                )
            elif self.optimizer == "brentq":
                self.constraints[0].V_init, info = scipy.optimize.brentq(
                    f=self.solve_scf_for_dW_by_dV,
                    a=self.constraints[0].V_brak[0],
                    b=self.constraints[0].V_brak[1],
                    maxiter=self.maxcscf
                )
            elif self.optimizer == "brenth":
                self.constraints[0].V_init, info = scipy.optimize.brenth(
                    f=self.solve_scf_for_dW_by_dV,
                    a=self.constraints[0].V_brak[0],
                    b=self.constraints[0].V_brak[1],
                    maxiter=self.maxcscf
                )
            elif self.optimizer in ["BFGS"]:
                res = scipy.optimize.minimize(
                    method=self.optimizer,
                    fun=self.solve_scf_with_new_V,
                    x0=np.array(list(c.V_init for c in self.constraints)),
                    jac=True,
                    options={"maxiter": self.maxcscf}
                )
                for c, V in zip(self.constraints, res.x):
                    c.V_init = V
            else:
                raise ValueError

        except CDFTSCFConverged:
            print("CDFTSolver: convergence achieved!")
        else:
            print("CDFTSolver: convergence NOT achieved after {} iterations.".format(self.maxcscf))

    def solve_scf_with_new_V(self, Vs):
        """ Given V for all constraints, solve KS problem."""
        self.itscf += 1

        # Update constraints
        for c, V in zip(self.constraints, Vs):
            c.V = V
            c.update_Vc()

        # Compute the total constraint potential Vc.
        self.Vc_tot = np.sum(c.Vc for c in self.constraints)

        # Impose the constraint potential Vc to DFT code.
        self.dft_driver.set_Vc(self.Vc_tot)

        # Order DFT code to perform SCF calculation under the constraint potential Vc.
        # After dft driver run_scf command should read etotal and force
        self.dft_driver.run_scf()
        self.dft_driver.get_rho_r()
        self.sample.W = self.sample.Edft_total - np.sum(c.V * c.N for c in self.constraints)

        # Print intermediate results
        print("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")
        print("Iter {}:".format(self.itscf))
        print("W (free energy) = {}".format(self.sample.W))
        print("E (DFT KS energy) = {}".format(self.sample.Edft_bare))
        print("Constraint info:")
        for i, c in enumerate(self.constraints):
            c.update_N()
            print("Constraint {} (type = {}, N0 = {}, V = {}):".format(
                i, c.type, c.N0, c.V
            ))
            print("N = {}".format(c.N))
            print("dW/dV = N - N0 = {}".format(c.dW_by_dV))

        if all(c.is_converged for c in self.constraints):
            raise CDFTSCFConverged

        # return the negative of W and dW/dV to be used by scipy minimizers
        return -self.sample.W, np.array(list(-c.dW_by_dV for c in self.constraints))

    def solve_scf_for_dW_by_dV(self, V):
        """ Wrapper function for solve_scf_with_new_V returning dW/dV."""
        return self.solve_scf_with_new_V([V])[1][0]

    def solve_opt(self):
        """ Relax the structure under constraint.

        A force from constraint potential is added to DFT force during optimization.
        """

        for istep in range(1, self.maxstep + 1):
            # run SCF to converge electronic structure
            self.solve_scf()

            # get DFT force
            Fdft = self.dft_driver.get_force()

            # compute constraint force
            for c in self.constraints:
                c.update_Fc()
            Fc = np.sum(c.Fc for c in self.constraints)

            Ftot = Fdft + Fc
            Ftotnorm = np.linalg.norm(Ftot, axis=1)
            maxforce = np.max(Ftotnorm)
            imaxforce = np.argmax(Ftotnorm)
            print("Maximum force = {} au, on {}th atom (Fdft = {}, Fc = {})".format(
                maxforce, imaxforce, Fdft[imaxforce], Fc[imaxforce]
            ))
            if maxforce < self.F_tol:
                print("CDFTSolver: force convergence achieved!")
                break

            # add constraint force to DFT force
            self.dft_driver.set_Fc(Fc)

            # run optimization
            self.dft_driver.run_opt()

            # parse updated coordinates
            self.dft_driver.get_structure()

            # update weights and constraint potentials
            for c in self.constraints:
                c.update_R(istep)

            print("================================")
            print("Structure updated")
            print("================================")

        else:
            print("CDFTSolver: optimization NOT achieved after {} steps.".format(self.maxstep))

    def copy(self):
        return deepcopy(self)
