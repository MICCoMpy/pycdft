from __future__ import absolute_import, division, print_function

import os
import re
import shutil
import time
import numpy as np
import xml.etree.ElementTree as ET
from ase.io.cube import read_cube_data, write_cube
from .base import DFTDriver


class QboxLockfileError(Exception):
    pass


class QboxDriver(DFTDriver):

    _sleep_seconds = 2
    _max_sleep_seconds = 3600 * 6

    _Vc_file = "Vc.cube"
    _rhor_file = "rhor.cube"

    _archive_folder = 'qbox_outputs'

    def __init__(self, sample, init_cmd, scf_cmd, opt_cmd="run 5 100 5", input_file="qb_cdft.in"):
        """Initialize QboxDriver.

        Control commands for SCF calculations (e.g. "run 0 100 5") needs to
        be specified by scf_cmd.
        """
        super(QboxDriver, self).__init__(sample)
        self._opt_cmd = opt_cmd
        self._init_cmd = init_cmd
        self._scf_cmd = scf_cmd
        self._input_file = input_file
        self._output_file = self._input_file.split('.')[0] + '.out'
        self._lock_file = "{}.lock".format(input_file)
        self.complete_input_file = "qb_complete.in"
        with open(self.complete_input_file, 'w') as file:
            file.write('')

        self.iter = 0

        self.scf_xml = None
        self.opt_xml = None

        if os.path.exists(self._archive_folder):
            shutil.rmtree(self._archive_folder)
            os.makedirs(self._archive_folder)
        else:
            os.makedirs(self._archive_folder)

        # Initialize Qbox
        print("QboxDriver: waiting for Qbox to start...")
        self._wait_lockfile()
        print("QboxDriver: initializing Qbox...")
        self._run_cmd(self._init_cmd)

    def _write_complete_input_file(self, cmd):
        with open(self.complete_input_file, 'a') as file:
            file.write(cmd + '\n')

    def _wait_lockfile(self):
        """ Wait for Qbox lock file to appear."""
        nsec = 0
        while not os.path.isfile(self._lock_file):
            time.sleep(self._sleep_seconds)
            nsec += self._sleep_seconds
            if nsec > self._max_sleep_seconds:
                raise QboxLockfileError

    def _run_cmd(self, cmd):
        """ Let Qbox run given command.

        Method returns when Qbox finishes running given command and lock file appears again.

        Args:
            cmd (str): Commands for Qbox.
        """
        open(self._input_file, "w").write(cmd + "\n")
        self._write_complete_input_file(cmd)

        os.remove(self._lock_file)
        self._wait_lockfile()

    def set_Vc(self, Vc):
        """ Implement abstract set_constraint_potential method for Qbox.

        Write Vc in cube format, then send set vext command to Qbox.
        """
        nspin = self.sample.nspin
        if nspin == 2:
            raise NotImplementedError("Spin-dependent Vext not implemented in Qbox yet.")
        fftgrid = self.sample.fftgrid
        n1, n2, n3 = fftgrid.n1, fftgrid.n2, fftgrid.n3
        assert isinstance(Vc, np.ndarray) and Vc.shape == (nspin, n1, n2, n3)

        ase_cell = self.sample.cell.ase_cell
        write_cube(open(self._Vc_file, "w"), atoms=ase_cell, data=Vc[0])

        self._run_cmd("set vext {}".format(self._Vc_file))

    def run_scf(self):
        """ Implement abstract run_scf method for Qbox."""
        self._run_cmd(self._scf_cmd)
        self.iter += 1

        shutil.copyfile(self._output_file,
                        "{}/iter{}.out".format(self._archive_folder, self.iter))

        self.scf_xml = ET.ElementTree(file=self._output_file).getroot()
        for i in self.scf_xml.findall('iteration'):
            self.sample.Etotal = float((i.findall('etotal'))[0].text)


    def run_opt(self):
        """ Implement abstract run_opt method for Qbox."""
        self._run_cmd(self._opt_cmd)
        shutil.copyfile(self._output_file,
                        "{}/iter{}_opt.out".format(self._archive_folder, self.iter))

        self.opt_xml = ET.ElementTree(file=self._output_file).getroot()


    def fetch_rhor(self):
        """ Implement abstract fetch_rhor method for Qbox.

        Send plot charge density commands to Qbox, then parse charge density.
        """
        nspin = self.sample.nspin

        for ispin in range(nspin):
            self._run_cmd(cmd="plot -density {} {}".format(
                "-spin {}".format(ispin + 1) if nspin == 2 else "",
                self._rhor_file
            ))

            rhor_raw = read_cube_data(self._rhor_file)[0]
            n1, n2, n3 = rhor_raw.shape

            rhor1 = np.roll(rhor_raw, n1//2, axis=0)
            rhor2 = np.roll(rhor1, n2//2, axis=1)
            rhor3 = np.roll(rhor2, n3//2, axis=2)
            self.sample.rhor[ispin] = rhor3


    def fetch_force(self):
        """ Implement abstract fetch_force method for Qbox."""
        # parse from self.scf_xml
        Fdft = np.zeros([self.sample.cell.natoms, 3])

        for i in self.scf_xml.findall('iteration')[-1:]:
            for atoms in i.findall('atomset'):
                for atom in atoms.findall('atom'):

                    m = re.match(r"([a-zA-Z]+)([0-9]+)", atom.attrib['name'])
                    symbol, index = m.group(1), int(m.group(2))
                    assert self.sample.cell.atoms[index-1].symbol == symbol

                    a = atom.findall('force')
                    f = np.array(a[0].text.split()).astype(np.float)

                    Fdft[index-1] = f

        return Fdft


    def set_force(self, Fc):
        """ Implement abstract set_force method for Qbox."""
        for i in range(self.sample.cell.natoms):
            symbol = self.sample.cell.atoms[i].symbol
            self._run_cmd(cmd="extforce delete f{}{}".format(symbol, i+1))

        for i in range(self.sample.cell.natoms):
            symbol = self.sample.cell.atoms[i].symbol
            qb_sym = symbol + str(i+1)
            self._run_cmd(cmd="extforce define f{} {} {:06f} {:06f} {:06f}".format(qb_sym, qb_sym, Fc[i][0], Fc[i][1], Fc[i][2]))


    def fetch_structure(self):
        """ Implement abstract fetch_structure method for Qbox."""
        # parse from self.scf_xml
        for i in self.opt_xml.findall('iteration')[-1:]:
            for atoms in i.findall('atomset'):
                for atom in atoms.findall('atom'):
                    m = re.match(r"([a-zA-Z]+)([0-9]+)", atom.attrib['name'])
                    symbol, index = m.group(1), int(m.group(2))
                    assert self.sample.cell.atoms[index - 1].symbol == symbol

                    a = atom.findall('position')
                    p = np.array(a[0].text.split()).astype(np.float)

                    self.sample.cell.atoms[index - 1].abs_coord = p


    def clean(self):
        """ Clean qb_cdft.in qb_cdft.out qb_cdft.in.lock"""
        self._run_cmd("save wf.xml")

        if os.path.exists(self._input_file):
            os.remove(self._input_file)

        if os.path.exists(self._lock_file):
            os.remove(self._lock_file)

        _output_file = self._input_file.split('.')[0] + '.out'
        if os.path.exists(_output_file):
            os.remove(_output_file)
