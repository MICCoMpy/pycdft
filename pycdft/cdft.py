import numpy as np
import os
import shutil

from .common import Sample
from .constraint import Constraint
from .dft_driver import DFTDriver


class CDFTSolver:
    """ Constrained DFT calculation.

    Attributes:
        job (str): "scf" or "relax".
        sample (Sample): the whole system for which CDFT calculation is performed.
        constraints (list of Constraint): constraints on the system.
        dft_driver (DFTDriver): the interface to DFT code (e.g. Qbox or PW).
        maxiter (int): maximum number of CDFT iterations.
        maxstep (int): maximum relaxation steps.
        Ftol (float): force threshold for relaxation.
        Vc_tot (float array, shape == [nspin, n1, n2, n3]): total constraint potential
            as a sum of all constraints defined on all fragments.
    """

    _archive_folder = 'outputs'

    def __init__(self, job: str, sample: Sample, dft_driver: DFTDriver,
                 maxiter=1000, maxstep=100, Ftol=1.0E-2):

        self.job = job
        self.sample = sample
        self.constraints = self.sample.constraints
        self.dft_driver = dft_driver
        self.maxiter = maxiter
        self.maxstep = maxstep
        self.Ftol = Ftol
        self.Vc_tot = None

        if os.path.exists(self._archive_folder):
            shutil.rmtree(self._archive_folder)
            os.makedirs(self._archive_folder)
        else:
            os.makedirs(self._archive_folder)

    def solve(self):
        """ Solve CDFT SCF or relax problem."""
        self.dft_driver.get_rho_r()
        if self.job == "scf":
            self.solve_scf()
        elif self.job == "relax":
            self.solve_relax()
        else:
            raise ValueError
        self.dft_driver.get_wfc()

    def solve_scf(self):
        """ Iteratively solve the CDFT problem.

        An outer loop (implemented below) is performed to maximize the free energy w.r.t.
        Lagrangian multipliers for all constrains, an inner loop (casted to a KS problem and
        outsourced to DFT code) is performed to minimize the free energy w.r.t. charge density.
        """

        self.sample.update_constraints()

        for c in self.constraints:
            c.optimizer.setup()

        for iiter in range(1, self.maxiter + 1):

            # Compute the total constraint potential Vc.
            self.Vc_tot = np.sum(c.Vc for c in self.constraints)

            # Impose the constraint potential Vc to DFT code.
            self.dft_driver.set_Vc(self.Vc_tot)

            # Order DFT code to perform SCF calculation under the constraint potential Vc.
            # After dft driver run_scf command should read etotal and force
            self.dft_driver.run_scf()
            self.dft_driver.get_rho_r()
            self.sample.Efree = self.sample.Edft_total - np.sum(c.V * c.N for c in self.constraints)
            print("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")
            print("Iter {}:".format(iiter))
            print("Free energy = {}".format(self.sample.Efree))
            print("DFT KS energy = {}".format(self.sample.Edft_bare))
            print("DFT KS+c energy = {}".format(self.sample.Edft_total))

            # Update all constraints.
            for c in self.constraints:
                c.update()

            if all(c.is_converged for c in self.constraints):
                print("CDFTSolver: convergence achieved!")
                if self.job == "relax":
                    self.dft_driver.get_force()
                return

        else:
            print("CDFTSolver: convergence NOT achieved after {} iterations.".format(self.maxiter))
            self.dft_driver.get_force()

    def solve_relax(self):
        """ Relax the structure under constraint.

        A force from constraint potential is added to DFT force during relaxation.
        """

        for istep in range(1, self.maxstep + 1):
            # run SCF to converge electronic structure
            self.solve_scf()

            maxforce, maxforceatom = self.sample.get_maxforce()
            print("Maximum force = {} au, on {}".format(maxforce, maxforceatom))
            if maxforce < self.Ftol:
                print("CDFTSolver: force convergence achieved!")
                break

            # add constraint force to DFT force
            Fc_total = np.zeros([self.sample.natoms, 3])
            for c in self.constraints:
                Fc_total += c.update_Fc()

            # add constraint force to DFT force
            self.dft_driver.set_Fc(Fc_total)

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
            print("CDFTSolver: relaxation NOT achieved after {} steps.".format(self.maxstep))
