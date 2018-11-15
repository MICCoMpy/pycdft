from random import randint
from subprocess import Popen
import warnings
import numpy as np
from ase import Atoms
from ase.io.cube import read_cube_data
from numpy.fft import *
from pycdft.atomic import rho_path, rd_grid, drd
from pycdft.atomic.pp import SG15PP
from pycdft.common.atom import Atom
from pycdft.common.units import angstrom_to_bohr, bohr_to_angstrom


class Sample(object):
    """ The physical system to be simulated.

    All physical quantities are in atomic unit.

    Variables ending with _r are defined on R space grid.
    Variables ending with _g are defined on G space grid.
    Variables ending with _rd are defined on radial grid in R space; variables ending
    with _d correspond to all G vectors with different norm. They are used to compute
    atomic densities.

    Attributes:
        R (np.ndarray, shape = [3, 3]): the real space lattice vectors of the system.
        G (np.ndarray, shape = [3, 3]): the reciprocal space lattice vectors of the system.
        omega (float): cell volume.
        atoms (list of Atoms): list of atoms.
        fragments (list of Fragments): list of fragments defined on the sample.
        rhopro_tot_r (np.ndarray, shape = [n1, n2, n3]): total promolecule density.
        vspin (int): number of spin channels (1 or 2) for constraint potential. Note that
                     as long as only charge constraints are present, vspin = 1 even if the
                     system may be spin-polarized.
        n1, n2, n3 (int): FFT grid for charge density, weight function and constraint potential.
        Ed (float): DFT energy.
        Ec (float): Constraint energy.
        W (float): free energy. W = Ed + Ec - sum_k V_k N_k
        Fd (float): DFT force.
        Fc (float): Constraint force. Fc = sum_k V_k int grad(w_k(r)) n(r) dr
        Fw (float): -grad(W). Fw = Fd + Fc.
    """

    def __init__(self, ase_cell: Atoms, vspin: int, n1: int, n2: int, n3: int,
                 atomic_density_files: dict=None):

        # define cell
        self.R = ase_cell.get_cell() * angstrom_to_bohr
        self.G = 2 * np.pi * np.linalg.inv(self.R).T
        assert np.all(np.isclose(np.dot(self.R, self.G.T), 2 * np.pi * np.eye(3)))
        self.omega = np.linalg.det(self.R)
        self.atoms = list(Atom(sample=self, ase_atom=atom) for atom in ase_cell)
        self.natoms = len(self.atoms)
        self.species = sorted(set([atom.symbol for atom in self.atoms]))
        self.nspecies = len(self.species)

        # define fragments and constraints list
        self.fragments = []
        self.constraints = []

        # define vspin and FFT grid
        self.vspin = vspin
        self.n1, self.n2, self.n3 = n1, n2, n3
        self.n = n1 * n2 * n3

        # define energies, forces and wavefunction
        self.Ed = None
        self.Ec = None
        self.W = None
        self.Fd = None
        self.Fc = None
        self.Fw = None
        self.wfc = None

        # define charge density and promolecule charge densities
        self.rho_r = None
        self.rhopro_tot_r = None
        self.rhoatom_g = {}
        self.rhoatom_rd = {}

        # compute the norm of all G vectors on the [n1, n2, n3] grid
        G1, G2, G3 = self.G
        # all Gx,Gy,Gz values
        G1s = np.outer(G1, fftfreq(n1, d=1. / n1))
        G2s = np.outer(G2, fftfreq(n2, d=1. / n2))
        G3s = np.outer(G3, fftfreq(n3, d=1. / n3))

        # make grid from G1s, G2s,G3s using Python built-in broadcasting
        self.Gx_g = (G1s[0, :, np.newaxis, np.newaxis]
                     + G2s[0, np.newaxis, :, np.newaxis]
                     + G3s[0, np.newaxis, np.newaxis, :])
        self.Gy_g = (G1s[1, :, np.newaxis, np.newaxis]
                     + G2s[1, np.newaxis, :, np.newaxis]
                     + G3s[1, np.newaxis, np.newaxis, :])
        self.Gz_g = (G1s[2, :, np.newaxis, np.newaxis]
                     + G2s[2, np.newaxis, :, np.newaxis]
                     + G3s[2, np.newaxis, np.newaxis, :])

        self.G2_g = self.Gx_g ** 2 + self.Gy_g ** 2 + self.Gz_g ** 2

        # compute atomic density for all species
        if atomic_density_files is not None:
            # read atomic density from file
            for s in self.species:
                rho_r, ase_cell = read_cube_data(atomic_density_files[s])
                omega = ase_cell.get_volume() * angstrom_to_bohr**3
                assert rho_r.shape == (n1, n2, n3)
                rho_r1 = np.roll(rho_r, n1 // 2, axis=0)
                rho_r2 = np.roll(rho_r1, n2 // 2, axis=1)
                rho_r3 = np.roll(rho_r2, n3 // 2, axis=2)
                self.rhoatom_g[s] = omega / self.n * fftn(rho_r3)
        else:
            # calculate atomic density from pre-computed spherically-averaged atomic density
            # located in atomic/rho

            # define mapping from all G vectors to G vectors with unique norm
            #  i.e., repeated |G| are excluded

            # tocheck: is 5A cutoff for atomic densities sufficient
            # tocheck: is 0.02 integration step sufficient
            
            # rd_grid size set to [251,]; 5 Ang cutoff with 0.02 Ang step
            # Gmapping, rho_g: n1 x n2 x n3 
            # sinrG: rd_grid x unique |G| 
            G2_d = np.sort(np.array(list(set(self.G2_g.flatten()))))
            self.Gmapping = np.searchsorted(G2_d, self.G2_g)
            self.G_d = np.sqrt(G2_d)
            self.sinrG = np.sin(np.outer(rd_grid, self.G_d))

            # rho(G) = 4 pi int( rho(r) r sinGr / G )
            # rho(G=0) = 4 pi int( rho(r) r^2 ) = nel / omega

            for s in self.species:
                rho_rd = np.loadtxt(
                    "{}/{}.spavr".format(rho_path, s), dtype=float)[:, 1]
                rho_rd[rho_rd < 0] = 0
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    rho_d = 4 * np.pi * drd * np.einsum("r,rg->g", rho_rd * rd_grid, self.sinrG / self.G_d)
                rho_g = rho_d[self.Gmapping]
                rho_g[0, 0, 0] = 4 * np.pi * drd * np.sum(rho_rd * rd_grid ** 2)
                fac = SG15PP[s]["nel"] / rho_g[0, 0, 0]
                rho_rd *= fac
                rho_g *= fac  # normalize charge density
                self.rhoatom_g[s] = rho_g
                self.rhoatom_rd[s] = rho_rd

    def update_weights(self):
        """ Update weights with new structure. """
        n = self.n1 * self.n2 * self.n3
        omega = self.omega

        # Update promolecule densities
        self.rhopro_tot_r = np.zeros([self.n1, self.n2, self.n3], dtype=np.complex_)
        for f in self.fragments:
            f.rhopro_r = np.zeros([self.n1, self.n2, self.n3], dtype=np.complex_)

        for atom in self.atoms:
            rhog = self.compute_rhoatom_g(atom)
            self.rhopro_tot_r += rhog
            for f in self.fragments:
                if atom in f.atoms:
                    f.rhopro_r += rhog

        self.rhopro_tot_r = (n / omega) * np.fft.ifftn(self.rhopro_tot_r).real  # FT G -> R
        for f in self.fragments:
            f.rhopro_r = (n / omega) * np.fft.ifftn(f.rhopro_r).real

        # Update weights
        for c in self.constraints:
            c.update_structure()

    def compute_eigr(self, atom: Atom, axis=None):
        """ Compute e^{-iGR} array where R is coordinate of atom."""
        n1, n2, n3 = self.n1, self.n2, self.n3

        if axis is None:
            r = atom.abs_coord
        else:
            r = np.zeros(3)
            r[axis] = atom.abs_coord[axis]

        igr1 = -1j * self.G[0] @ r
        igr2 = -1j * self.G[1] @ r
        igr3 = -1j * self.G[2] @ r

        hs = fftfreq(n1, d=1 / n1)
        ks = fftfreq(n2, d=1 / n2)
        ls = fftfreq(n3, d=1 / n3)

        eigr1 = np.exp(igr1 * hs)
        eigr2 = np.exp(igr2 * ks)
        eigr3 = np.exp(igr3 * ls)
        eigr = np.einsum("i,j,k->ijk", eigr1, eigr2, eigr3)

        return eigr

    def compute_rhoatom_g(self, atom: Atom):
        """ Compute charge density for an atom with specific coordinate in cell. """
        rhog0 = self.rhoatom_g[atom.symbol]
        eigr = self.compute_eigr(atom)
        return rhog0 * eigr

    def compute_rhoatom_grad_r(self, atom: Atom):
        """ Compute nuclear gradient for atom. """
        rhog = self.rhoatom_g[atom.symbol]
        n1, n2, n3 = self.n1, self.n2, self.n3
        rho_grad_r = np.zeros([3, n1, n2, n3])
        n = self.n
        omega = self.omega

        for i in range(3):
            eigr = self.compute_eigr(atom, axis=i)
            g = [self.Gx_g, self.Gy_g, self.Gz_g][i]
            rho_grad_r[i] = (n / omega) * ifftn(1j * g * eigr * rhog).real
            # print(eigr)

        return rho_grad_r

    @property
    def ase_cell(self):
        """ Get an ASE Atoms object of current cell."""
        ase_cell = Atoms(cell=self.R * bohr_to_angstrom)
        for atom in self.atoms:
            ase_cell.append(atom.ase_atom)
        return ase_cell

    def show(self):
        """ Visualize the structure by VESTA."""
        fname = "/tmp/cell{}.cif".format(randint(1000, 9999))
        self.save(fname)
        Popen(["vesta", fname])
        Popen(["sleep 120 && rm {}".format(fname)], shell=True)

    def save(self, fname):
        """ Save the structure to file."""
        self.ase_cell.write(fname)

    def export(self, fmt="qb", pseudos=None):
        """ Export the structure to various formats. """
        output = ""

        if fmt.lower() == "qb":
            output += "set cell {}\n".format(
                " ".join("{:06f}".format(self.R[i, j]) for i, j in np.ndindex(3, 3))
            )
            output += "\n"

            for species in self.species:
                output += "species {} {}\n".format(
                    species, pseudos[species] if pseudos else SG15PP[species]["xmlfile"]
                )
            output += "\n"

            for iatom, atom in enumerate(self.atoms):
                x, y, z = atom.abs_coord
                output += "atom {}{} {}  {:.8f}  {:.8f}  {:.8f}\n".format(
                    atom.symbol, iatom + 1, atom.symbol, x, y, z
                )

        else:
            raise NotImplementedError

        return output

    def nel(self, pseudos="SG15"):
        """ Compute # of electrons according to certain pseudopotential family."""
        if pseudos == "SG15":
            nel = 0
            for symbol in self.ase_cell.get_chemical_symbols():
                nel += SG15PP[symbol]["nel"]
        else:
            raise NotImplementedError
        return nel

    def __repr__(self):
        return "Cell \"{}\" natoms={}\nLattice constants:\n{}\nReciprocal lattice constants:\n{}\n".format(
            self.ase_cell.get_chemical_formula(), self.natoms, self.R, self.G
        )

    def __str__(self):
        return self.__repr__()
