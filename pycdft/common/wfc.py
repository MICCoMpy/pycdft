from __future__ import absolute_import, division, print_function

from .ft import *


class _CollectionManager:
    """Helper class to manage a collection of quantities like psi(r) or psi(G).

    The collection can be indexed by either an internal index or a (spin, kpoint, band) index.
    """

    def __init__(self, wfc, transform=lambda f: f):
        self._wfc = wfc
        self._qty = dict()
        self._transform = transform

    def indices(self):
        return self._qty.keys()

    def clear(self):
        self._qty.clear()

    def _get_idx(self, key):
        try:
            idx = int(key)
        except TypeError:
            try:
                ispin = int(key[0])
                ikpt = int(key[1])
                ibnd = int(key[2])
                assert 0 <= ispin <= self._wfc.nspin
                assert 0 <= ikpt <= self._wfc.nkpt
                assert 0 <= ibnd <= self._wfc.nbnd[ispin, ikpt]
            except ValueError:
                raise ValueError("Index must be either internal index or (spin, kpoint, band) index")
            except (AssertionError, IndexError):
                raise IndexError("(spin, kpoint, band) index out of range ({}, {}, {})".format(
                    self._wfc.nspin, self._wfc.nkpt, self._wfc.nbnd
                ))
            idx = self._wfc.skb2idx(ispin, ikpt, ibnd)
        return idx

    def __getitem__(self, key):
        try:
            return self._qty[self._get_idx(key)]
        except KeyError:
            return None

    def __setitem__(self, key, value):
        idx = self._get_idx(key)
        self._qty[idx] = self._transform(value)


class Wavefunction:
    """Container class for Kohn-Sham wavefunction

    A wavefunction is defined as a collection of KS orbitals, each uniquely labeled by
    three integers: spin (0 or 1), k point index and band index. To facilitate distributed
    storage and access of a wavefunction on multiple processors, each KS orbital is also
    uniquelly labeled by an internal index. Internal index (idx) is generated by following
    pattern:
        for ispin in range(nspin):
            for ikpt in range(nkpt):
                for ibnd in range(nbnd[ispin, ikpt]):
                    idx ++

    Currently, k points are not fully supported.

    Public attributes:
        psir: R space KS orbitals defined on a R space grid described by self.wgrid.
        psig: G space KS orbitals defined on a G space grid described by self.wgrid.
        psig_arr: G space KS orbitals defined on G vectors described by self.gvecs.

        Above quantities can be accessed like dicts. They can be indexed with either
        an integer (internal index) or a 3-tuple of integers (spin, kpoint, band index).
        After been indexed, the corresponding quantity (numpy array) of a
        specific KS orbital is returned.

        cell (Cell): cell upon which the wavefunction is defined.
        wgrid (FFTGrid): wavefunction grid.
        dgrid (FFTGrid): charge density grid.

        nspin (int): # of spin channel. 1: spin unpolarized; 2: spin polarized.
        nkpt (int): # of k points.
        nbnd (int): # of bands.
        norb (int): total # of orbitals on all spins, kpoints.
        occ (array): occupation numbers. shape: (nspin, nkpt, nbnd).

        gamma (bool): gamma-trick flag. The flag will affect how psig_arr is interpreted.
        gvecs (array): G vectors on which psig is defined. shape: (ng, 3)
        ngvecs (int): # of G vectors.

    Private attributes:
        _idx_skb_map (dict): internal index -> (spin, kpoint, band) index map
        _skb_idx_map (dict): (spin, kpoint, band) index -> internal index map

        Above maps can be accessed by skb2idx and idx2skb methods.
    """

    def __init__(self, cell, wgrid, dgrid, nspin, nkpt, nbnd, occ, gamma=True, gvecs=None):

        # define general info
        self._cell = cell
        self._wgrid = wgrid
        self._dgrid = dgrid

        self._nspin = nspin
        self._nkpt = nkpt
        assert self._nkpt == 1, "K points are not supported yet"
        try:
            nbnd_ = int(nbnd)
            # all spin and kpoints share the same nbnd
            self._nbnd = np.ones((self._nspin, self._nkpt), dtype=int) * nbnd_
        except TypeError:
            # every spin and kpoint have its own nbnd
            self._nbnd = np.array(nbnd, dtype=np.int_)
            assert self._nbnd.shape == (self._nspin, self._nkpt)
        if occ.ndim == 1:
            # all spin and kpoints share the same occupation
            self._occ = np.tile(occ, (self._nspin, self._nkpt)).reshape(self._nspin, self._nkpt, -1)
        else:
            # every spin and kpoint have its own occupation
            self._occ = np.zeros((self._nspin, self._nkpt, np.max(self._nbnd)), dtype=int)
            for ispin in range(self._nspin):
                for ikpt in range(self._nkpt):
                    nbnd = self.nbnd[ispin, ikpt]
                    self._occ[ispin, ikpt, 0:nbnd] = occ[ispin, ikpt][0:nbnd]

        self._gamma = gamma
        self._gvecs = gvecs

        # define maps between internal index <-> (spin, kpoint, band) index
        self._idx_skb_map = dict()
        idx = 0
        for ispin, ikpt in np.ndindex(self._nspin, self._nkpt):
            for ibnd in range(self._nbnd[ispin, ikpt]):
                self._idx_skb_map[idx] = (ispin, ikpt, ibnd)
                idx += 1
        self._norb = len(self._idx_skb_map)
        self._skb_idx_map = {
            self._idx_skb_map[idx]: idx
            for idx in range(self._norb)
        }

        # define containers to store collections of psi(r) or psi(G)
        self._psig_arr = _CollectionManager(self)
        self._psig = _CollectionManager(self)
        self._psir = _CollectionManager(self, transform=self.normalize)

        if gvecs is not None:
            assert gvecs.shape[1] == 3
            self._gvecs = gvecs
            self._ngvecs = gvecs.shape[0]
        else:
            # define G vectors according to QE convention
            # raise NotImplementedError
            pass

    def skb2idx(self, ispin, ikpt, ibnd):
        """Get internal index from (spin, kpoint, band) index."""
        try:
            return self._skb_idx_map[ispin, ikpt, ibnd]
        except KeyError:
            return None

    def idx2skb(self, idx):
        """Get (spin, kpoint, band) index from internal index."""
        return self._idx_skb_map[idx]

    def psig_arr2psig(self, psig_arr):
        """Match psi(G) defined on self.gvecs to G space grid defined on self.wgrid."""
        if self.gamma:
            psigd = embedd_g(psig_arr, self._gvecs, self._dgrid, fill="xyz")
            psig = ftgg(psigd, self._dgrid, self._wgrid)
        else:
            psigd = embedd_g(psig_arr, self._gvecs, self._dgrid)
            psig = ftgg(psigd, self._dgrid, self._wgrid)
        return psig

    def psig_arr2psir(self, psigarr, normalize=True):
        """Compute psi(r) from psi(G) defined on self.gvecs."""
        if self.gamma:
            psigd = embedd_g(psigarr, self.gvecs, self._dgrid, fill="yz")
            psig = ftgg(psigd, self._dgrid, self._wgrid, real=True)
            psir = ftgr(psig, self._wgrid, real=True)
        else:
            psigd = embedd_g(psigarr, self.gvecs, self._dgrid)
            psig = ftgg(psigd, self._dgrid, self._wgrid)
            psir = ftgr(psig, self._wgrid)

        if normalize:
            return self.normalize(psir)
        else:
            return psir

    def psig2psir(self, psig, normalize=True):
        """Compute psi(r) from psi(G) defined on selg.wgrid."""
        psir = ftgr(psig, self.wgrid, real=False)
        if normalize:
            return self.normalize(psir)
        else:
            return psir

    def compute_all_psig_from_psig_arr(self):
        """Match all psi(G) defined on self.gvecs to self.wgrid."""
        for idx in self._psig_arr.indices():
            self.psig[idx] = self.psig_arr2psig(self._psig_arr[idx])

    def compute_all_psir_from_psig_arr(self):
        """Compute all psi(r) based on psi(G) defined on self.gvecs."""
        for idx in self._psig_arr.indices():
            self.psir[idx] = self.psig_arr2psir(self._psig_arr[idx], normalize=False)

    def compute_all_psir_from_psig(self):
        """Compute all psi(r) based on psi(G) defined on self.wgrid."""
        for idx in self._psig.indices():
            self.psir[idx] = self.psig2psir(self._psig[idx], normalize=False)

    def normalize(self, psir):
        """Normalize psi(r)."""
        assert psir.shape == (self.wgrid.n1, self.wgrid.n2, self.wgrid.n3)
        norm = np.sqrt(np.sum(np.abs(psir) ** 2) * self.cell.omega / self.wgrid.N)
        return psir / norm

    @property
    def psig_arr(self):
        return self._psig_arr

    @property
    def psig(self):
        return self._psig

    @property
    def psir(self):
        return self._psir

    @property
    def cell(self):
        return self._cell

    @property
    def wgrid(self):
        return self._wgrid

    @property
    def dgrid(self):
        return self._dgrid

    @property
    def nspin(self):
        return self._nspin

    @property
    def nkpt(self):
        return self._nkpt

    @property
    def nbnd(self):
        return self._nbnd

    @property
    def norb(self):
        return self._norb

    @property
    def occ(self):
        return self._occ

    @property
    def gamma(self):
        return self._gamma

    @property
    def gvecs(self):
        return self._gvecs

    @property
    def ngvecs(self):
        return self._ngvecs