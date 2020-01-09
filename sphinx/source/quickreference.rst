.. _quickreference:

Quick Reference
===============

These are quick references for **pyCDFT** usage. 

Interactive Mode
----------------

One way to run PyCDFT is through an interactive session. 
This is ideal for debugging or small tutorial files. 
This involves having two terminals open: 1) terminal 1 runs the DFT driver in client-server mode in an interactive session and 2) terminal 2 loads a Jupyter notebook that can be run through a browser.

On Midway 2, you can request an `interactive session <https://rcc.uchicago.edu/docs/using-midway/index.html>`_ using e.g., 

.. code-block:: bash

   sinteractive --exclusive --partition=broadwl --nodes=2 --time=08:00:00

You can also run a `Jupyter Notebook <https://rcc.uchicago.edu/documentation/_build/html/software/environments/python/index.html>`_ on Midway using e.g., 

.. code-block:: bash

   ip=$(/sbin/ip route get 8.8.8.8 | awk '{print $NF;exit}')
   jupyter-notebook --no-browser --ip=${ip}

which will provide a link that you can click to open a Jupyter session.

Job Queue Mode
--------------

For larger jobs, the ability to submit in a job queue submission is also provided.

The basic command looks like

.. code-block:: bash

   export qb="/path/to/executable/qb_vext"
   mpirun -np 112 $qb -server qb_cdft.in qb_cdft.out &
   sleep 2
   python -u ${name}-thiophene-coupling.py > coupling_${name}.out

Example submission scripts for PBS and further details are provided in the respective tutorial files.

Template input file
-------------------

After compiling the DFT driver and installing **PyCDFT**, run the ground state calculation.
e.g., Qbox

.. code-block:: bash

   export qb="/path/to/executable"
   $qb < gs.in > gs.out

Then in the same directory, run Qbox in client-server mode (using interactive queue)

.. code-block:: bash

   mpirun -np <ntasks> $qb -server qb_cdft.in qb_cdft.out


A minimum working example for running **pyCDFT** and calculating the electron coupling parameter.

.. literalinclude:: ../../examples/02-he2_coupling/tut/he2_coupling-tmp.py
   :language: python

Each example comes with a Jupyter notebook that can be converted to a number of formats (e.g., Python script, html, pdf, LaTeX) using

.. code-block:: bash

   jupyter nbconvert --to FORMAT notebook.ipynb

where **FORMAT** takes on values described `here <https://ipython.org/ipython-doc/dev/notebook/nbconvert.html>`_.

Alternatively, you can use `ipynb-py-convert <https://pypi.org/project/ipynb-py-convert/>`.

Debugging
---------

To use the debugging module simply add the following commands to your python script. 
See :ref:`pycdft/pycdft.debug` for details. 

.. code-block:: bash

   get_hirsh(CDFTSolver, origin)
   get_hirsh_ct(CDFTSolver, origin)
   get_rho_atom(CDFTSolver, origin)
   get_rho(CDFTSolver, origin)
   get_grad(CDFTSolver, origin)

.. seealso:: 
   All input keywords are referenced and explained in the Manual. 
