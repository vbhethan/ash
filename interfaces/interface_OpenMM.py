import os
from sys import stdout
import time
import numpy as np
import glob
import copy
import itertools

#import ash
import ash.constants

ashpath = os.path.dirname(ash.__file__)
from ash.functions.functions_general import ashexit, BC, print_time_rel, listdiff, printdebug, print_line_with_mainheader, \
    print_line_with_subheader1, print_line_with_subheader2, isint, writelisttofile, writestringtofile, search_list_of_lists_for_index,create_conn_dict
from ash.functions.functions_elstructure import DDEC_calc, DDEC_to_LJparameters
from ash.modules.module_coords import Fragment, write_pdbfile, distance_between_atoms, list_of_masses, write_xyzfile, \
    change_origin_to_centroid, get_centroid, check_charge_mult, check_gradient_for_bad_atoms, get_molecule_members_loop_np2
from ash.modules.module_MM import UFF_modH_dict, MMforcefield_read
from ash.interfaces.interface_xtb import xTBTheory, grabatomcharges_xTB
from ash.interfaces.interface_ORCA import ORCATheory, grabatomcharges_ORCA, chargemodel_select
from ash.modules.module_singlepoint import Singlepoint
from ash.interfaces.interface_plumed import MTD_analyze
from ash.interfaces.interface_mdtraj import MDtraj_import, MDtraj_imagetraj, MDtraj_RMSF
import ash.functions.functions_parallel
import ash.modules.module_plotting

class OpenMMTheory:
    def __init__(self, printlevel=2, platform='CPU', numcores=1, topoforce=False, forcefield=None, topology=None,
                 CHARMMfiles=False, psffile=None, charmmtopfile=None, charmmprmfile=None,
                 GROMACSfiles=False, gromacstopfile=None, grofile=None, gromacstopdir=None,
                 Amberfiles=False, amberprmtopfile=None,
                 cluster_fragment=None, ASH_FF_file=None, PBCvectors=None,
                 xmlfiles=None, pdbfile=None, use_parmed=False,
                 xmlsystemfile=None,
                 do_energy_decomposition=False,
                 periodic=False, charmm_periodic_cell_dimensions=None, customnonbondedforce=False,
                 periodic_nonbonded_cutoff=12.0, dispersion_correction=True,
                 switching_function_distance=10.0,
                 ewalderrortolerance=5e-4, PMEparameters=None,
                 delete_QM1_MM1_bonded=False, applyconstraints_in_run=False,
                 constraints=None, restraints=None, frozen_atoms=None, fragment=None, dummysystem=False,
                 autoconstraints='HBonds', hydrogenmass=1.5, rigidwater=True, changed_masses=None):


        self.printlevel=printlevel
        if self.printlevel > 0:
            print_line_with_mainheader("OpenMM Theory")
        module_init_time = time.time()
        timeA = time.time()
        #Indicate that this is a MMtheory
        self.theorytype="MM"

        # OPEN MM load
        try:
            import openmm
            import openmm.app
            import openmm.unit
            if self.printlevel > 0:
                print("Imported OpenMM library version:", openmm.__version__)
        except ImportError:
            raise ImportError(
                "OpenMMTheory requires installing the OpenMM library. Try: conda install -c conda-forge openmm  \
                Also see http://docs.openmm.org/latest/userguide/application.html")

        # OpenMM variables
        # print(BC.WARNING, BC.BOLD, "------------Defining OpenMM object-------------", BC.END)
        if self.printlevel > 0:
            print_line_with_subheader1("Defining OpenMM object")
            print("Printlevel:", self.printlevel)
        # Initialize system
        self.system = None
        
        #Degrees of freedom of system (accounts for frozen atoms and constraints)
        #Will be set by compute_DOF
        self.dof=None

        # Load Parmed if requested
        if use_parmed is True:
            print("Using Parmed to read topologyfiles")
            try:
                import parmed
            except ImportError:
                print("Problem importing parmed Python library")
                print("Make sure parmed is present in your Python.")
                print("Parmed can be installed using pip: pip install parmed")
                ashexit(code=9)

        # Autoconstraints when creating MM system: Default: None,  Options: Hbonds, AllBonds, HAng
        if autoconstraints == 'HBonds':
            if self.printlevel > 0:
                print("HBonds option: X-H bond lengths will automatically be constrained")
            self.autoconstraints = openmm.app.HBonds
        elif autoconstraints == 'AllBonds':
            if self.printlevel > 0:
                print("AllBonds option: All bond lengths will automatically be constrained")
            self.autoconstraints = openmm.app.AllBonds
        elif autoconstraints == 'HAngles':
            if self.printlevel > 0:
                print("HAngles option: All bond lengths and H-X-H and H-O-X angles will automatically be constrained")
            self.autoconstraints = openmm.app.HAngles
        elif autoconstraints is None or autoconstraints == 'None':
            if self.printlevel > 0:
                print("No automatic constraints")
            self.autoconstraints = None
        else:
            print("Unknown autoconstraints option")
            ashexit()
        if self.printlevel > 0:
            print("AutoConstraint setting:", self.autoconstraints)
        
        # User constraints, restraints and frozen atoms
        self.user_frozen_atoms = []
        self.user_constraints = []
        self.user_restraints = []
        
        # Rigidwater constraints are on by default. Can be turned off
        self.rigidwater = rigidwater
        if self.printlevel > 0:
            print("Rigidwater constraints:", self.rigidwater)
        # Modify hydrogenmass or not
        if hydrogenmass is not None:
            self.hydrogenmass = hydrogenmass * openmm.unit.amu
        else:
            self.hydrogenmass = None
        if self.printlevel > 0:
            print("Hydrogenmass option:", self.hydrogenmass)

        # Setting for controlling whether QM1-MM1 bonded terms are deleted or not in a QM/MM job
        # See modify_bonded_forces
        # TODO: Move option to module_QMMM instead
        self.delete_QM1_MM1_bonded = delete_QM1_MM1_bonded
        # Platform (CPU, CUDA, OpenCL) and Parallelization
        self.platform_choice = platform
        # CPU: Control either by provided numcores keyword, or by setting env variable: $OPENMM_CPU_THREADS in shell
        # before running.
        self.numcores=numcores #Setting for general ASH compatibility
        self.properties = {}
        if self.platform_choice == 'CPU':
            if self.printlevel > 0:
                print("Using platform: CPU")
            self.properties["Threads"] = str(numcores)
            if numcores > 1:
                if self.printlevel > 0:
                    print("Numcores variable provided to OpenMM object. Will use {} cores with OpenMM".format(numcores))
                if self.printlevel > 0:
                    print(BC.WARNING,"Warning: Linux may ignore this user-setting and go with OPENMM_CPU_THREADS variable instead if set.",BC.END)
                    print("If OPENMM_CPU_THREADS was not set in jobscript, physical cores will probably be used.")
                    print("To be safe: check the running process on the node",BC.END)
            else:
                if self.printlevel > 0:
                    print("Numcores=1 or no numcores variable provided to OpenMM object")
                    print("Checking if OPENMM_CPU_THREADS shell variable is present")
                try:
                    if self.printlevel > 0:
                        print("OpenMM will use {} threads according to environment variable: OPENMM_CPU_THREADS".format(
                        os.environ["OPENMM_CPU_THREADS"]))
                except KeyError:
                    print(
                        "OPENMM_CPU_THREADS environment variable not set.\nOpenMM will choose number of physical cores "
                        "present.")
        else:
            if self.printlevel > 0:
                print("Using platform:", self.platform_choice)
        # Whether to do energy decomposition of MM energy or not. Takes time. Can be turned off for MD runs
        self.do_energy_decomposition = do_energy_decomposition

        # Initializing
        self.coords = []
        self.charges = []
        self.Periodic = periodic
        self.ewalderrortolerance = ewalderrortolerance

        # Whether to apply constraints or not when calculating MM energy via run method (does not apply to OpenMM MD)
        # NOTE: Should be False in general. Only True for special cases
        self.applyconstraints_in_run = applyconstraints_in_run

        # Switching function distance in Angstrom
        self.switching_function_distance = switching_function_distance

        # Residue names,ids,segments,atomtypes of all atoms of system.
        # Grabbed below from PSF-file. Information used to write PDB-file
        self.resnames = []
        self.resids = []
        self.segmentnames = []
        self.atomtypes = []
        self.atomnames = []
        self.mm_elements = []

        # Positions. Generally not used but can be if e.g. grofile has been read in.
        # Purpose: set virtual sites etc.
        self.positions = None

        


        self.Forcefield = None
        # What type of forcefield files to read. Reads in different way.
        # print("Now reading forcefield files")
        if self.printlevel > 0:
            print_line_with_subheader1("Setting up force fields.")
            print(
            "Note: OpenMM will fail in this step if parameters are missing in topology and\n"
            "      parameter files (e.g. nonbonded entries).\n")

        # #Always creates object we call self.forcefield that contains topology attribute
        if CHARMMfiles is True:
            if self.printlevel > 0:
                print("Reading CHARMM files.")
            self.psffile = psffile
            if use_parmed is True:
                if self.printlevel > 0:
                    print("Using Parmed.")
                self.psf = parmed.charmm.CharmmPsfFile(psffile)
                #Permissive True means less restrictive about atomtypes
                self.params = parmed.charmm.CharmmParameterSet(charmmtopfile, charmmprmfile, permissive=True)
                # Grab resnames from psf-object. Different for parmed object
                # Note: OpenMM uses 0-indexing
                self.resnames = [self.psf.atoms[i].residue.name for i in range(0, len(self.psf.atoms))]
                self.resids = [self.psf.atoms[i].residue.idx for i in range(0, len(self.psf.atoms))]
                self.segmentnames = [self.psf.atoms[i].residue.segid for i in range(0, len(self.psf.atoms))]
                self.atomtypes = [i.type for i in self.psf.atoms]
                # TODO: Note: For atomnames it seems OpenMM converts atomnames to its own. Perhaps not useful
                self.atomnames = [self.psf.atoms[i].name for i in range(0, len(self.psf.atoms))]

                #TODO: Elements are unset here. Parmed parses things differently
                #NOTE: we could deduce element from atomname or mass 
                #self.mm_elements = [self.psf.atoms[i].element for i in range(0, len(self.psf.atoms))]
                #self.mm_elements = [i.element.symbol for i in self.psf.topology.atoms()]
            else:
                # Load CHARMM PSF files via native routine.
                self.psf = openmm.app.CharmmPsfFile(psffile)
                self.params = openmm.app.CharmmParameterSet(charmmtopfile, charmmprmfile, permissive=True)
                # Grab resnames from psf-object
                self.resnames = [self.psf.atom_list[i].residue.resname for i in range(0, len(self.psf.atom_list))]
                self.resids = [self.psf.atom_list[i].residue.idx for i in range(0, len(self.psf.atom_list))]
                self.segmentnames = [self.psf.atom_list[i].system for i in range(0, len(self.psf.atom_list))]
                self.atomtypes = [self.psf.atom_list[i].attype for i in range(0, len(self.psf.atom_list))]
                # TODO: Note: For atomnames it seems OpenMM converts atomnames to its own. Perhaps not useful
                self.atomnames = [self.psf.atom_list[i].name for i in range(0, len(self.psf.atom_list))]
                self.mm_elements = [i.element.symbol for i in self.psf.topology.atoms()]

            self.topology = self.psf.topology
            self.forcefield = self.psf

        elif GROMACSfiles is True:
            if self.printlevel > 0:
                print("Reading Gromacs files.")
            # Reading grofile, not for coordinates but for periodic vectors
            if use_parmed is True:
                if self.printlevel > 0:
                    print("Using Parmed.")
                    print("GROMACS top dir:", gromacstopdir)
                parmed.gromacs.GROMACS_TOPDIR = gromacstopdir
                if self.printlevel > 0:
                    print("Reading GROMACS GRO file:", grofile)
                gmx_gro = parmed.gromacs.GromacsGroFile.parse(grofile)
                if self.printlevel > 0:
                    print("Reading GROMACS topology file:", gromacstopfile)
                gmx_top = parmed.gromacs.GromacsTopologyFile(gromacstopfile)

                # Getting PBC parameters
                gmx_top.box = gmx_gro.box
                gmx_top.positions = gmx_gro.positions
                self.positions = gmx_top.positions

                self.topology = gmx_top.topology
                self.forcefield = gmx_top

            else:
                if self.printlevel > 0:
                    print("Using built-in OpenMM routines to read GROMACS topology.")
                    print("WARNING: may fail if virtual sites present (e.g. TIP4P residues).")
                    print("Use 'parmed=True'  to avoid")
                gro = openmm.app.GromacsGroFile(grofile)
                self.grotop = openmm.app.GromacsTopFile(gromacstopfile, periodicBoxVectors=gro.getPeriodicBoxVectors(),
                                                        includeDir=gromacstopdir)

                self.topology = self.grotop.topology
                self.forcefield = self.grotop

            # TODO: Define resnames, resids, segmentnames, atomtypes, atomnames??


        elif Amberfiles is True:
            if self.printlevel > 0:
                print("Reading Amber files.")
                print("WARNING: Only new-style Amber7 prmtop-file will work.")
                print("WARNING: Will take periodic boundary conditions from prmtop file.")
            if use_parmed is True:
                if self.printlevel > 0:
                    print("Using Parmed to read Amber files.")
                self.prmtop = parmed.load_file(amberprmtopfile)
            else:
                if self.printlevel > 0:
                    print("Using built-in OpenMM routines to read Amber files.")
                # Note: Only new-style Amber7 prmtop files work
                self.prmtop = openmm.app.AmberPrmtopFile(amberprmtopfile)
            self.topology = self.prmtop.topology
            self.forcefield = self.prmtop

            #List of resids, resnames and mm_elements. Used by actregiondefine
            self.resids = [i.residue.index for i in self.prmtop.topology.atoms()]
            self.resnames = [i.residue.name for i in self.prmtop.topology.atoms()]
            self.mm_elements = [i.element.symbol for i in self.prmtop.topology.atoms()]
            #NOTE: OpenMM does not grab Amber atomtypes for some reason. Feature request
            #TODO: Grab more topology information
            # TODO: Define segmentnames, atomtypes, atomnames??


        elif topoforce is True:
            if self.printlevel > 0:
                print("Using forcefield info from topology and forcefield keyword.")
            self.topology = topology
            self.forcefield = forcefield

        elif ASH_FF_file is not None:
            if self.printlevel > 0:
                print("Reading ASH cluster fragment file and ASH Forcefield file.")

            # Converting ASH FF file to OpenMM XML file
            MM_forcefield = MMforcefield_read(ASH_FF_file)

            atomtypes_res = []
            atomnames_res = []
            elements_res = []
            atomcharges_res = []
            sigmas_res = []
            epsilons_res = []
            residue_types = []
            masses_res = []

            for resid, residuetype in enumerate(MM_forcefield['residues']):
                residue_types.append("RS" + str(resid))
                atypelist = MM_forcefield[residuetype + "_atomtypes"]
                # atypelist needs to be more unique due to different charges
                atomtypes_res.append(["R" + residuetype[-1] + str(j) for j, i in enumerate(atypelist)])
                elements_res.append(MM_forcefield[residuetype + "_elements"])
                atomcharges_res.append(MM_forcefield[residuetype + "_charges"])
                # Atomnames, have to be unique and 4 letters, adding number
                atomnames_res.append(["R" + residuetype[-1] + str(j) for j, i in enumerate(atypelist)])
                sigmas_res.append([MM_forcefield[atomtype].LJparameters[0] / 10 for atomtype in
                                   MM_forcefield[residuetype + "_atomtypes"]])
                epsilons_res.append([MM_forcefield[atomtype].LJparameters[1] * 4.184 for atomtype in
                                     MM_forcefield[residuetype + "_atomtypes"]])
                masses_res.append(list_of_masses(elements_res[-1]))

            xmlfile = write_xmlfile_nonbonded(resnames=residue_types, atomnames_per_res=atomnames_res,
                                              atomtypes_per_res=atomtypes_res,
                                              elements_per_res=elements_res, masses_per_res=masses_res,
                                              charges_per_res=atomcharges_res, sigmas_per_res=sigmas_res,
                                              epsilons_per_res=epsilons_res,
                                              filename="cluster_system.xml", coulomb14scale=1.0, lj14scale=1.0)
            # Creating lists for PDB-file
            # requires ffragmenttype_labels to be present in fragment.
            # NOTE: Hence will only work for molcrys-prepared files for now
            atomnames_full = []
            jindex = 0
            resid_index = 1
            residlabels = []
            residue_types_full = []
            for i, fragtypelabel in enumerate(cluster_fragment.fragmenttype_labels):
                atomnames_full.append(atomnames_res[fragtypelabel][jindex])
                residlabels.append(resid_index)
                jindex += 1
                residue_types_full.append("RS" + str(fragtypelabel))
                if jindex == len(atomnames_res[fragtypelabel]):
                    jindex = 0
                    resid_index += 1

            # Creating PDB-file, only for topology (not coordinates)
            write_pdbfile(cluster_fragment, outputname="cluster", resnames=residue_types_full, atomnames=atomnames_full,
                          residlabels=residlabels)
            pdb = openmm.app.PDBFile("cluster.pdb")
            self.topology = pdb.topology

            self.forcefield = openmm.app.ForceField(xmlfile)

        # Load XMLfile for whole system
        elif xmlsystemfile is not None:
            if self.printlevel > 0:
                print("Reading system XML file:", xmlsystemfile)
            xmlsystemfileobj = open(xmlsystemfile).read()
            # Deserialize the XML text to create a System object.
            if self.printlevel > 0:
                print("Now defining OpenMM system using information in file")
                print("Warning: file may contain hardcoded constraints that can not be overridden.")
            self.system = openmm.XmlSerializer.deserializeSystem(xmlsystemfileobj)
            #self.forcefield = system_temp.forcefield
            #NOTE: Big drawback of xmlsystemfile is that constraints have been hardcoded and can
            #NOTE: we could remove all present constraints using: self.remove_all_constraints()
            #NOTE: However, not sure how easy to enforce Hatom, rigidwater etc. constraints again without remaking system object
            #NOTE: Maybe define system object using XmlSerializer, somehow create forcefield object from it.
            #NOTE: Then recreate system below. Not sure if possible

            #TODO: set further properties of system here, e.g. PME parameters
            #otherwise system is not completely set

            # We still need topology from somewhere to using pdbfile
            if self.printlevel > 0:
                print("Reading topology from PDBfile:", pdbfile)
            pdb = openmm.app.PDBFile(pdbfile)
            self.topology = pdb.topology
        # Simple OpenMM system without any forcefield defined. Requires ASH fragment
        # Used for OpenMM_MD with QM Hamiltonian
        elif dummysystem is True:
            #Create list of atomnames, used in PDB topology and XML file
            atomnames_full=[j+str(i) for i,j in enumerate(fragment.elems)]
            #Write PDB-file frag.pdb with dummy atomnames
            write_pdbfile(fragment, outputname="frag", atomnames=atomnames_full)
            #Load PDB-file and create topology
            pdb = openmm.app.PDBFile("frag.pdb")
            self.topology = pdb.topology

            #Create dummy XML file
            xmlfile = write_xmlfile_nonbonded(filename="dummy.xml", resnames=["DUM"], atomnames_per_res=[atomnames_full], atomtypes_per_res=[fragment.elems],
                                            elements_per_res=[fragment.elems], masses_per_res=[fragment.masses],
                                            charges_per_res=[[0.0]*fragment.numatoms],
                                            sigmas_per_res=[[0.0]*fragment.numatoms], epsilons_per_res=[[0.0]*fragment.numatoms], skip_nb=True)
            #Create dummy forcefield
            self.forcefield = openmm.app.ForceField(xmlfile)


        # Read topology from PDB-file and XML-forcefield files to define forcefield
        else:
            if self.printlevel > 0:
                print("Reading OpenMM XML forcefield files and PDB file")
                print("xmlfiles:", str(xmlfiles).strip("[]"))
                if pdbfile == None:
                    print("Error:No pdbfile input provided")
                    ashexit()
            # This would be regular OpenMM Forcefield definition requiring XML file
            # Topology from PDBfile annoyingly enough
            pdb = openmm.app.PDBFile(pdbfile)
            self.topology = pdb.topology
            # Todo: support multiple xml file here
            # forcefield = simtk.openmm.app.ForceField('amber14-all.xml', 'amber14/tip3pfb.xml')
            self.forcefield = openmm.app.ForceField(*xmlfiles)

            #Defining some things. resids is used by actregiondefine
            self.resids = [i.residue.index for i in self.topology.atoms()]




        # NOW CREATE SYSTEM UNLESS already created (xmlsystemfile)
        if self.system is None:
            # Periodic or non-periodic ystem
            if self.Periodic is True:
                if self.printlevel > 0:
                    print_line_with_subheader1("Setting up periodicity.")
                    print("Nonbonded cutoff is {} Angstrom.".format(periodic_nonbonded_cutoff))
                # Parameters here are based on OpenMM DHFR example

                if CHARMMfiles is True:
                    if self.printlevel > 0:
                        print("Using CHARMM files.")

                    if charmm_periodic_cell_dimensions is None:
                        print(
                            "Error: When using CHARMMfiles and 'Periodic=True', 'charmm_periodic_cell_dimensions' "
                            "keyword needs to be supplied.")
                        print(
                            "Example: charmm_periodic_cell_dimensions= [200, 200, 200, 90, 90, 90]  in Angstrom and "
                            "degrees")
                        ashexit()
                    self.charmm_periodic_cell_dimensions = charmm_periodic_cell_dimensions
                    if self.printlevel > 0:
                        print("Periodic cell dimensions:", charmm_periodic_cell_dimensions)
                    self.a = charmm_periodic_cell_dimensions[0] * openmm.unit.angstroms
                    self.b = charmm_periodic_cell_dimensions[1] * openmm.unit.angstroms
                    self.c = charmm_periodic_cell_dimensions[2] * openmm.unit.angstroms
                    if use_parmed is True:
                        self.forcefield.box = [self.a, self.b, self.c, charmm_periodic_cell_dimensions[3],
                                               charmm_periodic_cell_dimensions[4], charmm_periodic_cell_dimensions[5]]
                        # print("Set box vectors:", self.forcefield.box)
                        if self.printlevel > 0:
                            print_line_with_subheader2("Set box vectors:")
                            print("a:", self.a)
                            print("b:", self.b)
                            print("c:", self.c)
                            print("alpha:", charmm_periodic_cell_dimensions[3])
                            print("beta:", charmm_periodic_cell_dimensions[4])
                            print("gamma:", charmm_periodic_cell_dimensions[5])
                    else:
                        self.forcefield.setBox(self.a, self.b, self.c,
                                               alpha=openmm.unit.Quantity(value=charmm_periodic_cell_dimensions[3],
                                                                        unit=openmm.unit.degree),
                                               beta=openmm.unit.Quantity(value=charmm_periodic_cell_dimensions[3],
                                                                       unit=openmm.unit.degree),
                                               gamma=openmm.unit.Quantity(value=charmm_periodic_cell_dimensions[3],
                                                                        unit=openmm.unit.degree))
                        if self.printlevel > 0:
                            print("Set box vectors:", self.forcefield.box_vectors)


                    self.system = self.forcefield.createSystem(self.params, nonbondedMethod=openmm.app.PME,
                                                               constraints=self.autoconstraints,
                                                               hydrogenMass=self.hydrogenmass,
                                                               rigidWater=self.rigidwater, ewaldErrorTolerance=self.ewalderrortolerance,
                                                               nonbondedCutoff=periodic_nonbonded_cutoff * openmm.unit.angstroms,
                                                               switchDistance=switching_function_distance * openmm.unit.angstroms)
                elif GROMACSfiles is True:
                    # NOTE: Gromacs has read PBC info from Gro file already
                    if self.printlevel > 0:
                        print("Ewald Error tolerance:", self.ewalderrortolerance)
                    # Note: Turned off switchDistance. Not available for GROMACS?
                    #
                    self.system = self.forcefield.createSystem(nonbondedMethod=openmm.app.PME,
                                                               constraints=self.autoconstraints,
                                                               hydrogenMass=self.hydrogenmass,
                                                               rigidWater=self.rigidwater, ewaldErrorTolerance=self.ewalderrortolerance,
                                                               nonbondedCutoff=periodic_nonbonded_cutoff * openmm.unit.angstroms)
                elif Amberfiles is True:
                    # NOTE: Amber-interface has read PBC info from prmtop file already
                    self.system = self.forcefield.createSystem(nonbondedMethod=openmm.app.PME,
                                                               constraints=self.autoconstraints,
                                                               hydrogenMass=self.hydrogenmass,
                                                               rigidWater=self.rigidwater, ewaldErrorTolerance=self.ewalderrortolerance,
                                                               nonbondedCutoff=periodic_nonbonded_cutoff * openmm.unit.angstroms)

                    # print("self.system num con", self.system.getNumConstraints())
                else:
                    if self.printlevel > 0:
                        print("Setting up periodic system here.")
                    # Modeller and manual xmlfiles
                    self.system = self.forcefield.createSystem(self.topology, nonbondedMethod=openmm.app.PME,
                                                               constraints=self.autoconstraints,
                                                               hydrogenMass=self.hydrogenmass,
                                                               rigidWater=self.rigidwater, ewaldErrorTolerance=self.ewalderrortolerance,
                                                               nonbondedCutoff=periodic_nonbonded_cutoff * openmm.unit.angstroms)
                    # switchDistance=switching_function_distance*self.unit.angstroms

                # print("self.system dict", self.system.__dict__)

                # TODO: Customnonbonded force option. Currently disabled

                if PBCvectors is not None:
                    # pbcvectors_mod = PBCvectors
                    if self.printlevel > 0:
                        print("Setting PBC vectors by user request.")
                        print("Assuming list of lists or list of Vec3 objects.")
                        print("Assuming vectors in nanometers.")
                    self.system.setDefaultPeriodicBoxVectors(*PBCvectors)

                a, b, c = self.system.getDefaultPeriodicBoxVectors()
                if self.printlevel > 0:
                    print_line_with_subheader2("Periodic vectors:")
                    print(a)
                    print(b)
                    print(c)
                # print("Periodic vectors:", self.system.getDefaultPeriodicBoxVectors())
                print("")
                # Force modification here
                # print("OpenMM Forces defined:", self.system.getForces())
                if self.printlevel > 0:
                    print_line_with_subheader2("OpenMM Forces defined:")
                for force in self.system.getForces():
                    if self.printlevel > 0:
                        print(force.getName())
                    #NONBONDED FORCE 
                    if isinstance(force, openmm.CustomNonbondedForce):
                        # NOTE: THIS IS CURRENTLY NOT USED
                        pass
                    elif isinstance(force, openmm.NonbondedForce):

                        # Turn Dispersion correction on/off depending on user
                        force.setUseDispersionCorrection(dispersion_correction)

                        # Modify PME Parameters if desired
                        # force.setPMEParameters(1.0/0.34, fftx, ffty, fftz)
                        if PMEparameters is not None:
                            if self.printlevel > 0:
                                print("Changing PME parameters")
                            force.setPMEParameters(PMEparameters[0], PMEparameters[1], PMEparameters[2],
                                                   PMEparameters[3])
                        # force.setSwitchingDistance(switching_function_distance)
                        # if switching_function is True:
                        #    force.setUseSwitchingFunction(switching_function)
                        #    #Switching distance in nm. To be looked at further
                        #   force.setSwitchingDistance(switching_function_distance)
                        #    print('SwitchingFunction distance: %s' % force.getSwitchingDistance())
                        if self.printlevel > 0:
                            print_line_with_subheader2("Nonbonded force settings (after all modifications):")
                            print("Periodic cutoff distance: {}".format(force.getCutoffDistance()))
                            print('Use SwitchingFunction: %s' % force.getUseSwitchingFunction())
                        if force.getUseSwitchingFunction() is True:
                            if self.printlevel > 0:
                                print('SwitchingFunction distance: {}'.format(force.getSwitchingDistance()))
                        if self.printlevel > 0:
                            print('Use Long-range Dispersion correction: %s' % force.getUseDispersionCorrection())
                            print("PME Parameters:", force.getPMEParameters())
                            print("Ewald error tolerance:", force.getEwaldErrorTolerance())

                if self.printlevel > 0:
                    print_line_with_subheader2("OpenMM system created.")

            # Non-Periodic
            else:
                if self.printlevel > 0:
                    print("System is non-periodic.")

                if CHARMMfiles is True:
                    self.system = self.forcefield.createSystem(self.params, nonbondedMethod=openmm.app.NoCutoff,
                                                               constraints=self.autoconstraints,
                                                               rigidWater=self.rigidwater,
                                                               nonbondedCutoff=1000 * openmm.unit.angstroms,
                                                               hydrogenMass=self.hydrogenmass)
                elif Amberfiles is True:
                    self.system = self.forcefield.createSystem(nonbondedMethod=openmm.app.NoCutoff,
                                                               constraints=self.autoconstraints,
                                                               rigidWater=self.rigidwater,
                                                               nonbondedCutoff=1000 * openmm.unit.angstroms,
                                                               hydrogenMass=self.hydrogenmass)
                #NOTE: might be unnecessary
                elif dummysystem is True:
                    self. system = self.forcefield.createSystem(self.topology)
                else:
                    self.system = self.forcefield.createSystem(self.topology, nonbondedMethod=openmm.app.NoCutoff,
                                                               constraints=self.autoconstraints,
                                                               rigidWater=self.rigidwater,
                                                               nonbondedCutoff=1000 * openmm.unit.angstroms,
                                                               hydrogenMass=self.hydrogenmass)
                if self.printlevel > 0:
                    print_line_with_subheader2("OpenMM system created.")
                    print("OpenMM Forces defined:", self.system.getForces())
                    print("")
                # for i,force in enumerate(self.system.getForces()):
                #    if isinstance(force, openmm.NonbondedForce):
                #        self.getatomcharges()
                #        self.nonbonded_force=force

                # print("original forces: ", forces)
                # Get charges from OpenMM object into self.charges
                # self.getatomcharges(forces['NonbondedForce'])
                # print("self.system.getForces():", self.system.getForces())
                # self.getatomcharges(self.system.getForces()[6])

                # CASE CUSTOMNONBONDED FORCE
                # REPLACING REGULAR NONBONDED FORCE
                if customnonbondedforce is True:
                    print("currently inactive")
                    ashexit()
                    # Create CustomNonbonded force
                    for i, force in enumerate(self.system.getForces()):
                        if isinstance(force, openmm.NonbondedForce):
                            custom_nonbonded_force, custom_bond_force = create_cnb(self.system.getForces()[i])
                    print("1custom_nonbonded_force:", custom_nonbonded_force)
                    print("num exclusions in customnonb:", custom_nonbonded_force.getNumExclusions())
                    print("num 14 exceptions in custom_bond_force:", custom_bond_force.getNumBonds())

                    # TODO: Deal with frozen regions. NOT YET DONE
                    # Frozen-Act interaction
                    # custom_nonbonded_force.addInteractionGroup(self.frozen_atoms,self.active_atoms)
                    # Act-Act interaction
                    # custom_nonbonded_force.addInteractionGroup(self.active_atoms,self.active_atoms)
                    # print("2custom_nonbonded_force:", custom_nonbonded_force)

                    # Pointing self.nonbonded_force to CustomNonBondedForce instead of Nonbonded force
                    self.nonbonded_force = custom_nonbonded_force
                    print("self.nonbonded_force:", self.nonbonded_force)
                    self.custom_bondforce = custom_bond_force

                    # Update system with new forces and delete old force
                    self.system.addForce(self.nonbonded_force)
                    self.system.addForce(self.custom_bondforce)

                    # Remove oldNonbondedForce
                    for i, force in enumerate(self.system.getForces()):
                        if isinstance(force, openmm.NonbondedForce):
                            self.system.removeForce(i)

        # Defining nonbonded force
        for i, force in enumerate(self.system.getForces()):
            if isinstance(force, openmm.NonbondedForce):
                # self.getatomcharges()
                self.nonbonded_force = force

        # Set charges in OpenMMobject by taking from Force (used by QM/MM)
        if self.printlevel > 0:
            print("Setting charges")
        # self.getatomcharges(self.nonbonded_force)
        self.getatomcharges()
        
        # Storing numatoms and list of all atoms
        self.numatoms = int(self.system.getNumParticles())
        self.allatoms = list(range(0, self.numatoms))
        if self.printlevel > 0:
            print("Number of atoms in OpenMM system:", self.numatoms)

        # Preserve original masses before any mass modifications or frozen atoms (set mass to 0)
        #NOTE: Creates list of Quantity objects (value, unit attributes)
        self.system_masses_original = [self.system.getParticleMass(i) for i in self.allatoms]
        #List of currently used masses. Can be modified by self.modify_masses and self.freeze_atoms
        #NOTE: Regular list of floats
        self.system_masses = [self.system.getParticleMass(i)._value for i in self.allatoms]


        if constraints or frozen_atoms or restraints:
            if self.printlevel > 0:
                print_line_with_subheader1("Adding user constraints, restraints or frozen atoms.")
        # Now adding user-defined system constraints (only bond-constraints supported for now)
        if constraints is not None:
            if self.printlevel > 0:
                print("Before adding user constraints, system contains {} constraints".format(self.system.getNumConstraints()))
                print("")

            if len(constraints) < 50:
                print("User-constraints to add:", constraints)
            else:
                print(f"{len(constraints)} user-defined constraints to add.")

            # Cleaning up constraint list. Adding distance if missing
            if 2 in [len(con) for con in constraints]:
                print("Missing distance value for some constraints. Can apply current-geometry distances if ASH\n"
                      "fragment has been provided")
                if fragment is None:
                    print("No ASH fragment provided to OpenMMTheory. Will check if pdbfile is defined and use coordinates from there")
                    if pdbfile is None:
                        print("No PDBfile present either. Either fragment or PDBfile containing \
                            coordinates is required for constraint definition")
                        ashexit()
                    else:
                        fragment=Fragment(pdbfile=pdbfile)
                # Cleaning up constraint list. Adding distance if missing
                constraints = clean_up_constraints_list(fragment=fragment, constraints=constraints)
            self.user_constraints = constraints
            print("")
            self.add_bondconstraints(constraints=constraints)
            print("")
            # print("After adding user constraints, system contains {} constraints".format(self.system.getNumConstraints()))
            if self.printlevel > 0:
                print(f"{len(self.user_constraints)} user-defined constraints added.")
        # Now adding user-defined frozen atoms
        if frozen_atoms is not None:
            self.user_frozen_atoms = frozen_atoms
            if len(self.user_frozen_atoms) < 50:
                print("Frozen atoms to add:", str(frozen_atoms).strip("[]"))
            else:
                print(f"{len(self.user_frozen_atoms)} user-defined frozen atoms to add.")
            self.freeze_atoms(frozen_atoms=frozen_atoms)
        
        # Now adding user-defined restraints (only bond-restraints supported for now)
        if restraints is not None:
            # restraints is a list of lists defining bond restraints: constraints = [[atom_i,atom_j, d, k ]]
            # Example: [[700,701, 1.05, 5.0 ]] Unit is Angstrom and kcal/mol * Angstrom^-2
            self.user_restraints = restraints
            if len(self.user_restraints) < 50:
                print("User-restraints to add:", restraints)
            else:
                print(f"{len(self.user_restraints)} user-defined restraints to add.")
            self.add_bondrestraints(restraints=restraints)

        #Now changing masses if requested
        if changed_masses is not None:
            if self.printlevel > 0:
                print("Modified masses")
            #changed_masses should be a dict of : atomindex: mass
            self.modify_masses(changed_masses=changed_masses)
        
        if self.printlevel > 0:
            print("\nSystem constraints defined upon system creation:", self.system.getNumConstraints())
            print("Use printlevel =>3 to see list of all constraints")
        if self.printlevel >= 3:
            for i in range(0, self.system.getNumConstraints()):
                print("Defined constraints:", self.system.getConstraintParameters(i))
        #print_time_rel(timeA, modulename="system create")
        timeA = time.time()
        


        #Set simulation parameters (here just default options)
        self.set_simulation_parameters()

        #Now calling function to compute the actual degrees of freedom.
        #NOTE: Needs to be called once, after system-create, constraints and frozen atoms are done.
        self.compute_DOF()

        #Force run. Option to allow run even though constraints may be defined
        #Used by GentlewarmupMD etc. to get a basic gradient
        self.force_run=False

        # Create/update basic simulation (will be overridden by OpenMM_Opt, OpenMM_MD functions)
        #Disabling as we want to make OpenMMTheory picklable
        #update_simulation needs to be called instead by run
        #self.create_simulation()

        print_time_rel(module_init_time, modulename="OpenMM object creation")

    #Set numcores method: currently inactive. Included for completeness
    def set_numcores(self,numcores):
        self.numcores=numcores
    #Set numcores method
    def cleanup(self):
        print("Cleanup for OpenMMTheory called")

    # add force that restrains atoms to a fixed point:
    # https://github.com/openmm/openmm/issues/2568

    # To set positions in OpenMMobject (in nm) from np-array (Angstrom)
    def set_positions(self, coords,simulation):
        import openmm
        print("Setting coordinates of OpenMM object")
        coords_nm = coords * 0.1  # converting from Angstrom to nm
        pos = [openmm.Vec3(coords_nm[i, 0], coords_nm[i, 1], coords_nm[i, 2]) for i in
               range(len(coords_nm))] * openmm.unit.nanometer
        simulation.context.setPositions(pos)
        print("Coordinates set")

    #Add dummy 
    #https://simtk.org/plugins/phpBB/viewtopicPhpbb.php?f=161&t=10049&p=0&start=0&view=&sid=b844250e55b14682fb21b5f66a4d810f
    #https://github.com/openmm/openmm/issues/2262
    #Helpful for NPT simulations when solute is fixed
    #TODO: Not quiteready. Not sure how to use best
    # Add dummy atom for each solute atom?
    # Or enought to add like a centroid atom and then bind each solute atom via restraint?
    def add_dummy_atom_to_restrain_solute(self,atomindices=None, forceconstant=100):
        import openmm
        print("num particles", self.system.getNumParticles())
        #Adding dummy atom with mass 0
        self.system.addParticle(0)
        print("num particles", self.system.getNumParticles())
        dummyatomindex=self.system.getNumParticles()-1
        print("dummyatomindex:", dummyatomindex)
        #Adding zero-charge and zero-epsilon to Nonbonded force (charge,sigma,epsilon)
        self.nonbonded_force.addParticle(0, 1, 0)
        #Adding dummy-atom to topology
        chain=self.topology.addChain()
        residue=self.topology.addResidue("dummy",chain)
        dummy_element=openmm.app.element.Element(0,"Dummyel","Dd",0.0)
        self.topology.addAtom("Dum",dummy_element,residue)

        self.restraint = openmm.HarmonicBondForce()
        self.restraint.setUsesPeriodicBoundaryConditions(True)
        self.system.addForce(self.restraint)

        for i in atomindices:
            print("Adding bond")
            self.restraint.addBond(i, dummyatomindex, 0, forceconstant)
        #for force in self.system.getForces():
        #    if isinstance(force,openmm.HarmonicBondForce):
        #        print("Adding harmonic bond to dummy atom and atomindex 1")
        #        #Add harmonic bond between first atom in solute
        #        for i in atomindices:
        #            print("Adding bond")
        #            force.addBond(i, dummyatomindex, 0, 20)
    
    #NOTE: we probably can not remove particles actually
    # TOBE DELETED
    def remove_dummy_atom(self):
        #Go through atom labels/names and delete if it has a dummy label ?
        
        #Or remove by index ?

        #1. remove system particle
        
        #2. remove nonbonded force info ?
        #3. remove from topology
        #4. remove system restraint force ?
        self.system.removeForce(-1)


    #Option to make sure small solute in water behaves for PBC
    #NOTE: DOes not work
    def add_custom_bond_force(self,i,j,forceconstant):
        import openmm
        print(f"Adding custom bond force between atom index i={i} and j={j} with forceconstant={forceconstant}")
        bond_force = openmm.CustomBondForce("0.5*k*(r-r0)^2")
        bond_force.addGlobalParameter("k", forceconstant)
        bond_force.addGlobalParameter("r0", 1.0)
        #bond_force = openmm.HarmonicBondForce()
        #bond_force.addBond(i,j,0.0,forceconstant)
        bond_force.addBond(i, j)
        print("bond_force getBondParameters:", bond_force.getBondParameters(0))
        bond_force.setUsesPeriodicBoundaryConditions(True)
        self.system.addForce(bond_force)
    
    # This is custom externa force that restrains group of atoms to center of system
    def add_center_force(self, center_coords=None, atomindices=None, forceconstant=1.0):
        import openmm
        print("Inside add_center_force")
        print("center_coords:", center_coords)
        print("atomindices:", atomindices)
        print("forceconstant:", forceconstant)
        #Distinguish periodic and nonperiodic scenarios:
        if self.Periodic is True:
            print("Warning: Add_center_force with PBC is not tested")
            centerforce = openmm.CustomExternalForce("k *periodicdistance(x, y, z, x0, y0, z0)")    
        else:
            centerforce = openmm.CustomExternalForce("k * (abs(x-x0) + abs(y-y0) + abs(z-z0))")
        centerforce.addGlobalParameter("k",
                                       forceconstant * 4.184 * openmm.unit.kilojoule / openmm.unit.angstrom / openmm.unit.mole)
        centerforce.addPerParticleParameter('x0')
        centerforce.addPerParticleParameter('y0')
        centerforce.addPerParticleParameter('z0')
        # Coordinates of system center
        center_x = center_coords[0] / 10
        center_y = center_coords[1] / 10
        center_z = center_coords[2] / 10
        for i in atomindices:
            # centerforce.addParticle(i, np.array([0.0, 0.0, 0.0]))
            centerforce.addParticle(i, openmm.Vec3(center_x, center_y, center_z))
        self.system.addForce(centerforce)
        #Updating simulation again in order to update parameters. Making sure not to change integrator etc.
        #self.create_simulation(timestep=self.timestep, integrator=self.integrator, 
        #                       coupling_frequency=self.coupling_frequency, temperature=self.temperature)
        print("Added center force")
        return centerforce

    def add_custom_external_force(self):
        import openmm
        # customforce=None
        # inspired by https://github.com/CCQC/janus/blob/ba70224cd7872541d279caf0487387104c8253e6/janus/mm_wrapper/openmm_wrapper.py
        customforce = openmm.CustomExternalForce("-x*fx -y*fy -z*fz")
        # customforce.addGlobalParameter('shift', 0.0)
        customforce.addPerParticleParameter('fx')
        customforce.addPerParticleParameter('fy')
        customforce.addPerParticleParameter('fz')
        for i in range(self.system.getNumParticles()):
            customforce.addParticle(i, np.array([0.0, 0.0, 0.0]))
        self.system.addForce(customforce)
        # self.externalforce=customforce
        # Necessary:
        #self.create_simulation(timestep=self.timestep, integrator=self.integrator, 
        #                       coupling_frequency=self.coupling_frequency, temperature=self.temperature)
        #self.update_simulation()
        # http://docs.openmm.org/latest/api-c++/generated/OpenMM.CustomExternalForce.html

        print("Added force")
        return customforce

    #NOTE: This can take some time but not sure we can make this faster
    def update_custom_external_force(self, customforce, gradient, simulation, conversion_factor=49614.752589207):
        if self.printlevel >= 2:
            print("Updating custom external force")
        # shiftpar_inkjmol=shiftparameter*2625.4996394799
        # Convert Eh/Bohr gradient to force in kj/mol nm
        # *49614.501681716106452
        #NOTE: default conversion factor (49614.752589207) assumes input gradient in Eh/Bohr and converting to kJ/mol nm
        forces = -gradient * conversion_factor
        for i, f in enumerate(forces):
            customforce.setParticleParameters(i, i, f)
        # print("xx")
        # self.externalforce.X(shiftparameter)
        # NOTE: updateParametersInContext expensive. Avoid somehow???
        # https://github.com/openmm/openmm/issues/1892
        # print("Current value of global par 0:", self.externalforce.getGlobalParameterDefaultValue(0))
        # self.externalforce.setGlobalParameterDefaultValue(0, shiftpar_inkjmol)
        # print("Current value of global par 0:", self.externalforce.getGlobalParameterDefaultValue(0))
        customforce.updateParametersInContext(simulation.context)

    # Function to add restraints to system before MD
    def add_bondrestraints(self, restraints=None):
        print("Adding restraints:", restraints)
        import openmm
        new_restraints = openmm.HarmonicBondForce()
        for i, j, d, k in restraints:
            print(
                "Adding bond restraint between atoms {} and {}. Distance value: {} Å. Force constant: {} kcal/mol*Å^-2".format(
                    i, j, d, k))
            new_restraints.addBond(i, j, d * openmm.unit.angstroms,
                                   k * openmm.unit.kilocalories_per_mole / openmm.unit.angstroms ** 2)
        self.system.addForce(new_restraints)

    # TODO: Angleconstraints and Dihedral restraints

    #For restraining CVs, used by metadynamics
    #NOTE: Assuming Angstrom and kcal/mol^2 here like for regular restraints
    #NOTE: Dihedrals not supported (unclear if useful). Angles are and units are radians
    def add_CV_restraint(self,cvforce,restraint_par,cvtype):
        import openmm
        #Make copy of CVforce (otherwise we can not use it also in restraint)
        cvforce_copy=copy.copy(cvforce)
        #TODO: periodic CV vs non-periodic
        if cvtype == "dihedral" or cvtype == "torsion":
            print("Adding CV restraints for dihedrals is not available!")
            ashexit()
            #Not sure whether there is ever a need
            #energy_expression = f"0.5*k*(1-cos(var-var_max))"
        elif cvtype == "angle":
            print("Adding CV restraints for angles is not available!")
            ashexit()
            energy_expression = f"(k/2)*max(0, var-var_max)^2"
            print("CV type: angle")
            print("Note: unit assumed to be in radians")
            var_unit = openmm.unit.radian
            var_unit_label="radians"
        elif cvtype == "bond" or cvtype == "distance" or cvtype == "rmsd" :
            energy_expression = f"(k/2)*max(0, var-var_max)^2"
            print("CV type: bond/rmsd")
            print("Note: unit assumed be in Angstrom")
            var_unit = openmm.unit.angstroms
            var_unit_label="Å"
        else:
            print("Error: unknown cvtype for add_CV_restraint")
            ashexit()
        #Energy unit
        energy_unit = openmm.unit.kilocalories_per_mole / openmm.unit.angstroms ** 2
        energy_unit_label="kcal/mol*Å^-2"
        #Periodic:
        print("Adding restraint with energy expression:", energy_expression)
        print(f"Max value (var_max): {restraint_par[0]} {var_unit_label}")
        print(f"Force constant (k) : {restraint_par[1]} {energy_unit_label}")
        restraint_force_CV = openmm.CustomCVForce(energy_expression)
        restraint_force_CV.addCollectiveVariable('var', cvforce_copy)
        restraint_force_CV.addGlobalParameter('var_max', restraint_par[0]*var_unit)
        restraint_force_CV.addGlobalParameter("k", restraint_par[1]*energy_unit)                    
        self.system.addForce(restraint_force_CV)

    # Write XML-file for full system
    def saveXML(self, xmlfile="system_full.xml"):
        import openmm
        serialized_system = openmm.XmlSerializer.serialize(self.system)
        with open(xmlfile, 'w') as f:
            f.write(serialized_system)
        print("Wrote system XML file:", xmlfile)

    # Function to add bond constraints to system before MD
    def add_bondconstraints(self, constraints=None):
        import openmm
        for i, j, d in constraints:
            print("Adding bond constraint between atoms {} and {}. Distance value: {:.4f} Å".format(i, j, d))
            self.system.addConstraint(i, j, d * openmm.unit.angstroms)

    #Remove all defined constraints in system
    def remove_all_constraints(self):
        todelete=[]
        # Looping over all defined system constraints
        for i in range(0, self.system.getNumConstraints()):
            todelete.append(i)
        for d in reversed(todelete):
            self.system.removeConstraint(d)
    #Remove specific constraints
    def remove_constraints(self, constraints):
        todelete = []
        # Looping over all defined system constraints
        for i in range(0, self.system.getNumConstraints()):
            con = self.system.getConstraintParameters(i)
            for usercon in constraints:
                if all(elem in usercon for elem in [con[0], con[1]]):
                    todelete.append(i)
        for d in reversed(todelete):
            self.system.removeConstraint(d)
    #Remove constraints for selected atoms. For example: QM atoms in QM/MM MD
    def remove_constraints_for_atoms(self, atoms):
        print("Removing constraints in OpenMM object for atoms:", atoms)
        todelete = []
        # Looping over all defined system constraints
        for i in range(0, self.system.getNumConstraints()):
            con = self.system.getConstraintParameters(i)
            #print("con:", con)
            if con[0] in atoms or con[1] in atoms:
                todelete.append(i)
        for d in reversed(todelete):
            self.system.removeConstraint(d)

    # Function to freeze atoms during OpenMM MD simulation. Sets masses to zero. Does not modify potential
    # energy-function.
    def freeze_atoms(self, frozen_atoms=None):
        import openmm
        print("Freezing {} atoms by setting particles masses to zero.".format(len(frozen_atoms)))

        # Modify particle masses in system object. For freezing atoms
        for i in frozen_atoms:
            self.system.setParticleMass(i, 0 * openmm.unit.daltons)
        
        #Update list of current masses
        self.system_masses = [self.system.getParticleMass(i)._value for i in self.allatoms]

    #Changed masses according to user input dictionary
    def modify_masses(self, changed_masses=None):
        import openmm
        print("Modify masses according: ", changed_masses)
        # Preserve original masses
        #self.system_masses = [self.system.getParticleMass(i) for i in self.allatoms]
        # Modify particle masses in system object.
        for am in changed_masses:
            self.system.setParticleMass(am, changed_masses[am] * openmm.unit.daltons)

        #Update list of current masses
        self.system_masses = [self.system.getParticleMass(i)._value for i in self.allatoms]

    def unfreeze_atoms(self):
        # Looping over system_masses if frozen, otherwise empty list
        for atom, mass in zip(self.allatoms, self.system_masses_original):
            self.system.setParticleMass(atom, mass)

        #Update list of current masses
        self.system_masses = [self.system.getParticleMass(i)._value for i in self.allatoms]

    # Currently unused
    def set_active_and_frozen_regions(self, active_atoms=None, frozen_atoms=None):
        # FROZEN AND ACTIVE ATOMS
        self.allatoms = list(range(0, self.numatoms))
        if active_atoms is None and frozen_atoms is None:
            print("All {} atoms active, no atoms frozen".format(len(self.allatoms)))
            self.frozen_atoms = []
        elif active_atoms is not None and frozen_atoms is None:
            self.active_atoms = active_atoms
            self.frozen_atoms = listdiff(self.allatoms, self.active_atoms)
            print("{} active atoms, {} frozen atoms".format(len(self.active_atoms), len(self.frozen_atoms)))
            # listdiff
        elif frozen_atoms is not None and active_atoms is None:
            self.frozen_atoms = frozen_atoms
            self.active_atoms = listdiff(self.allatoms, self.frozen_atoms)
            print("{} active atoms, {} frozen atoms".format(len(self.active_atoms), len(self.frozen_atoms)))
        else:
            print("active_atoms and frozen_atoms can not be both defined")
            ashexit()

    # This removes interactions between particles in a region (e.g. QM-QM or frozen-frozen pairs)
    # Give list of atom indices for which we will remove all pairs
    # Todo: Way too slow to do for big list of e.g. frozen atoms but works well for qmatoms list size
    # Alternative: Remove force interaction and then add in the interaction of active atoms to frozen atoms
    # should be reasonably fast
    # https://github.com/openmm/openmm/issues/2124
    # https://github.com/openmm/openmm/issues/1696
    def addexceptions(self, atomlist):
        import openmm
        timeA = time.time()
        import itertools
        print("Add exceptions/exclusions. Removing i-j interactions for list:", len(atomlist), "atoms")

        # Has duplicates
        # [self.nonbonded_force.addException(i,j,0, 0, 0, replace=True) for i in atomlist for j in atomlist]
        # https://stackoverflow.com/questions/942543/operation-on-every-pair-of-element-in-a-list
        # [self.nonbonded_force.addException(i,j,0, 0, 0, replace=True) for i,j in itertools.combinations(atomlist, r=2)]
        numexceptions = 0
        numexclusions = 0
        printdebug("self.system.getForces() ", self.system.getForces())
        # print("self.nonbonded_force:", self.nonbonded_force)

        for force in self.system.getForces():
            printdebug("force:", force)
            if isinstance(force, openmm.NonbondedForce):
                print("Case Nonbondedforce. Adding Exception for ij pair.")
                for i in atomlist:
                    for j in atomlist:
                        printdebug("i,j : {} and {} ".format(i, j))
                        force.addException(i, j, 0, 0, 0, replace=True)

                        # NOTE: Case where there is also a CustomNonbonded force present (GROMACS interface).
                        # Then we have to add exclusion there too to avoid this issue: https://github.com/choderalab/perses/issues/357
                        # Basically both nonbonded forces have to have same exclusions (or exception where chargepro=0, eps=0)
                        # TODO: This leads to : Exception: CustomNonbondedForce: Multiple exclusions are specified for particles
                        # Basically we have to inspect what is actually present in CustomNonbondedForce
                        # for force in self.system.getForces():
                        #    if isinstance(force, openmm.CustomNonbondedForce):
                        #        force.addExclusion(i,j)

                        numexceptions += 1
            elif isinstance(force, openmm.CustomNonbondedForce):
                print("Case CustomNonbondedforce. Adding Exclusion for kl pair.")
                # NOTE: This step is unfortunately a bit slow (43 seconds for 28 atomlist in 71K system)
                # Only applies to system with CustomNonbondedForce (e.g. GROMACS setup)
                # TODO: look into speeding up
                # Get list of all present exclusions first
                all_exclusions = [force.getExclusionParticles(exclindex) for exclindex in range(0,force.getNumExclusions()) ]
                # Function 
                def check_if_exclusion_present(all_exclusions,pair):
                    for exclusion in all_exclusions:
                        if set(exclusion) == set(pair):
                            return True
                    return False
                for k in atomlist:
                    for l in atomlist:
                        if check_if_exclusion_present(all_exclusions,(k,l)) is False:
                            all_exclusions.append([k,l])
                            force.addExclusion(k, l)
                            numexclusions += 1
        print("Number of exceptions (Nonbondedforce) added:", numexceptions)
        print("Number of exclusions (CustomNonbondedforce) added:", numexclusions)
        printdebug("self.system.getForces() ", self.system.getForces())
        # Seems like updateParametersInContext does not reliably work here so we have to remake the simulation instead
        # Might be bug (https://github.com/openmm/openmm/issues/2709). Revisit
        # self.nonbonded_force.updateParametersInContext(self.simulation.context)
        #self.create_simulation()
        #self.update_simulation()

        print_time_rel(timeA, modulename="add exception")

    # Run: coords or framents can be given (usually coords). qmatoms in order to avoid QM-QM interactions (TODO)
    # Probably best to do QM-QM exclusions etc. in a separate function though as we want run to be as simple as possible
    # qmatoms list provided for generality of MM objects. Not used here for now

    def set_simulation_parameters(self, timestep=0.001, coupling_frequency=1, temperature=300, integrator='VerletIntegrator'):
        self.timestep=timestep
        self.coupling_frequency=coupling_frequency
        self.temperature=temperature
        self.integrator_name=integrator
    # Create/update simulation from scratch or after system has been modified (force modification or even deletion)
    #def create_simulation(self, timestep=0.001, integrator='VerletIntegrator', coupling_frequency=1,
    #                      temperature=300):

    #Create integrator.
    def create_integrator(self):
        timeA = time.time()
        import openmm
        #NOTE: Integrator definition has to be here (instead of set_simulation_parameters) as it has to be recreated for each updated simulation
        # Integrators: LangevinIntegrator, LangevinMiddleIntegrator, NoseHooverIntegrator, VerletIntegrator,
        # BrownianIntegrator, VariableLangevinIntegrator, VariableVerletIntegrator
        if self.integrator_name == 'VerletIntegrator':
            self.integrator = openmm.VerletIntegrator(self.timestep * openmm.unit.picoseconds)
        elif self.integrator_name == 'VariableVerletIntegrator':
            self.integrator = openmm.VariableVerletIntegrator(self.timestep * openmm.unit.picoseconds)
        elif self.integrator_name == 'LangevinIntegrator':
            self.integrator = openmm.LangevinIntegrator(self.temperature * openmm.unit.kelvin,
                                                             self.coupling_frequency / openmm.unit.picosecond,
                                                             self.timestep * openmm.unit.picoseconds)
        elif self.integrator_name == 'LangevinMiddleIntegrator':
            # openmm recommended with 4 fs timestep, Hbonds 1/ps friction
            self.integrator = openmm.LangevinMiddleIntegrator(self.temperature * openmm.unit.kelvin,
                                                                   self.coupling_frequency / openmm.unit.picosecond,
                                                                   self.timestep * openmm.unit.picoseconds)
        elif self.integrator_name == 'NoseHooverIntegrator':
            self.integrator = openmm.NoseHooverIntegrator(self.temperature * openmm.unit.kelvin,
                                                               self.coupling_frequency / openmm.unit.picosecond,
                                                               self.timestep * openmm.unit.picoseconds)
        # NOTE: Problem with Brownian, disabling
        # elif integrator == 'BrownianIntegrator':
        #    self.integrator = openmm.BrownianIntegrator(temperature*self.unit.kelvin, coupling_frequency/self.unit.picosecond, timestep*self.unit.picoseconds)
        elif self.integrator_name == 'VariableLangevinIntegrator':
            self.integrator = openmm.VariableLangevinIntegrator(self.temperature * openmm.unit.kelvin,
                                                                     self.coupling_frequency / openmm.unit.picosecond,
                                                                     self.timestep * openmm.unit.picoseconds)
        else:
            print(BC.FAIL,
                  "Unknown integrator.\n Valid integrator keywords are: VerletIntegrator, VariableVerletIntegrator, "
                  "LangevinIntegrator, LangevinMiddleIntegrator, NoseHooverIntegrator, VariableLangevinIntegrator ",
                  BC.END)
            ashexit()
        print_time_rel(timeA, modulename="create integrator")
    
    #Create simulation object (now not part of OpenMMTheory)
    def create_simulation(self, internal=False):
        timeA = time.time()
        import openmm

        if self.printlevel > 0:
            print_line_with_subheader1("Creating/updating OpenMM simulation object")
            print("Integrator name:", self.integrator_name)
            print("Timestep:", self.timestep)
            print("Temperature:", self.temperature)
            print("Coupling frequency:", self.coupling_frequency)
            print("Properties:", self.properties)
            print("Topology:", self.topology)
        printdebug("self.system.getForces() ", self.system.getForces())

        #Create integrator object (needed for every update)
        self.create_integrator()

        #Create simulation, either as part of OpenMMTheory (not picklable)
        #or not (used by run method)
        if internal is True:
            #NOTE: Not sure if needed anymore
            self.simulation = openmm.app.simulation.Simulation(self.topology, self.system, self.integrator, 
                                                            openmm.Platform.getPlatformByName(self.platform_choice),
                                                                self.properties)
            return
        else:
            simulation = openmm.app.simulation.Simulation(self.topology, self.system, self.integrator, 
                                                            openmm.Platform.getPlatformByName(self.platform_choice),
                                                                self.properties)
            print_time_rel(timeA, modulename="creating/updating simulation")
            return simulation


    # Functions for energy decompositions
    def forcegroupify(self):
        self.forcegroups = {}
        print("inside forcegroupify")
        print("self.system.getForces()", self.system.getForces())
        print("Number of forces:\n", self.system.getNumForces())
        for i in range(self.system.getNumForces()):
            force = self.system.getForce(i)
            force.setForceGroup(i)
            self.forcegroups[force] = i
        # print("self.forcegroups :", self.forcegroups)
        # ashexit()

    def getEnergyDecomposition(self, context):
        # Call and set force groups
        self.forcegroupify()
        energies = {}
        # print("self.forcegroups:", self.forcegroups)
        for f, i in self.forcegroups.items():
            energies[f] = context.getState(getEnergy=True, groups=2 ** i).getPotentialEnergy()
        return energies

    def printEnergyDecomposition(self,simulation):
        import openmm
        timeA = time.time()
        # Energy composition
        # TODO: Calling this is expensive (seconds)as the energy has to be recalculated.
        # Only do for cases: a) single-point b) First energy-step in optimization and last energy-step
        # OpenMM energy components
        openmm_energy = dict()
        energycomp = self.getEnergyDecomposition(simulation.context)
        # print("energycomp: ", energycomp)
        # print("self.forcegroups:", self.forcegroups)
        # print("len energycomp", len(energycomp))
        # print("openmm_energy: ", openmm_energy)
        print("")
        bondterm_set = False
        extrafcount = 0
        # This currently assumes CHARMM36 components, More to be added
        for comp in energycomp.items():
            # print("comp: ", comp)
            if 'HarmonicBondForce' in str(type(comp[0])):
                # Not sure if this works in general.
                if bondterm_set is False:
                    openmm_energy['Bond'] = comp[1]
                    bondterm_set = True
                else:
                    openmm_energy['Urey-Bradley'] = comp[1]
            elif 'HarmonicAngleForce' in str(type(comp[0])):
                openmm_energy['Angle'] = comp[1]
            elif 'PeriodicTorsionForce' in str(type(comp[0])):
                # print("Here")
                openmm_energy['Dihedrals'] = comp[1]
            elif 'CustomTorsionForce' in str(type(comp[0])):
                openmm_energy['Impropers'] = comp[1]
            elif 'CMAPTorsionForce' in str(type(comp[0])):
                openmm_energy['CMAP'] = comp[1]
            elif 'NonbondedForce' in str(type(comp[0])):
                openmm_energy['Nonbonded'] = comp[1]
            elif 'CMMotionRemover' in str(type(comp[0])):
                openmm_energy['CMM'] = comp[1]
            elif 'CustomBondForce' in str(type(comp[0])):
                openmm_energy['14-LJ'] = comp[1]
            else:
                extrafcount += 1
                openmm_energy['Otherforce' + str(extrafcount)] = comp[1]

        print_time_rel(timeA, modulename="energy decomposition")
        # timeA = time.time()

        # The force terms to print in the ordered table.
        # Deprecated. Better to print everything.
        # Missing terms in force_terms will be printed separately
        # if self.Forcefield == 'CHARMM':
        #    force_terms = ['Bond', 'Angle', 'Urey-Bradley', 'Dihedrals', 'Impropers', 'CMAP', 'Nonbonded', '14-LJ']
        # else:
        #    #Modify...
        #    force_terms = ['Bond', 'Angle', 'Urey-Bradley', 'Dihedrals', 'Impropers', 'CMAP', 'Nonbonded']

        # Sum all force-terms
        sumofallcomponents = 0.0
        for val in openmm_energy.values():
            sumofallcomponents += val._value

        # Print energy table
        print('%-20s | %-15s | %-15s' % ('Component', 'kJ/mol', 'kcal/mol'))
        print('-' * 56)
        # TODO: Figure out better sorting of terms
        for name in sorted(openmm_energy):
            print('%-20s | %15.2f | %15.2f' % (name, openmm_energy[name] / openmm.unit.kilojoules_per_mole,
                                               openmm_energy[name] / openmm.unit.kilocalorie_per_mole))
        print('-' * 56)
        print('%-20s | %15.2f | %15.2f' % ('Sumcomponents', sumofallcomponents, sumofallcomponents / 4.184))
        print("")
        print('%-20s | %15.2f | %15.2f' % ('Total', self.energy * ash.constants.hartokj, self.energy * ash.constants.harkcal))

        print("")
        print("")
        # Adding sum to table
        openmm_energy['Sum'] = sumofallcomponents
        self.energy_components = openmm_energy

    # Compute the number of degrees of freedom.
    def compute_DOF(self):
        import openmm
        dof = 0
        for i in range(self.system.getNumParticles()):
            if self.system.getParticleMass(i) > 0*openmm.unit.dalton:
                dof += 3
        for i in range(self.system.getNumConstraints()):
            p1, p2, distance = self.system.getConstraintParameters(i)
            if self.system.getParticleMass(p1) > 0*openmm.unit.dalton or self.system.getParticleMass(p2) > 0*openmm.unit.dalton:
                dof -= 1
        if any(type(self.system.getForce(i)) == openmm.CMMotionRemover for i in range(self.system.getNumForces())):
            dof -= 3
        self.dof=dof

    #NOTE: Adding charge/mult here temporarily to  be consistent with QM_theories. Not used
    def run(self, current_coords=None, elems=None, Grad=False, fragment=None, qmatoms=None, label=None, charge=None, mult=None,
            numcores=1):
        module_init_time = time.time()
        timeA = time.time()
        import openmm

        #Need to call create_simulation here in order to get a simulation object
        simulation = self.create_simulation()

        # timeA = time.time()
        if self.printlevel > 1:
            print_line_with_subheader1("Running Single-point OpenMM Interface")
        # If no coords given to run then a single-point job probably (not part of Optimizer or MD which would supply
        # coords). Then try if fragment object was supplied.
        # Otherwise internal coords if they exist
        if current_coords is None:
            if fragment is None:
                if len(self.coords) != 0:
                    if self.printlevel > 1:
                        print("Using internal coordinates (from OpenMM object).")
                    current_coords = self.coords
                else:
                    print("Found no coordinates!")
                    ashexit()
            else:
                current_coords = fragment.coords

        #IMPORTANT: Checking whether constraints have been defined in OpenMM object
        # Defined OpenMM constraints will not work within a Single-point run scheme
        # In fact forces will be all wrong. Thus checking before continuing
        # Constraints and frozen atoms have to instead by enforced by geomeTRICOptimizer, non-OpenMM dynamics module etc.
        defined_constraints=self.system.getNumConstraints()
        if self.printlevel > 1:
            print("Number of OpenMM system constraints defined:", defined_constraints)

        if self.autoconstraints != None or self.rigidwater==True:
            print(BC.FAIL,"OpenMM autoconstraints (HBonds,AllBonds,HAngles) in OpenMMTheory are not compatible with OpenMMTheory.run()", BC.END)
            print(BC.WARNING,"Please redefine OpenMMTheory object: autoconstraints=None, rigidwater=False", BC.END)
            if self.force_run is True:
                print("force_run is True. Will continue")
            else:
                ashexit()
            
        if self.user_frozen_atoms or self.user_constraints or self.user_restraints:
            print("User-defined frozen atoms/constraints/restraints in OpemmTheory are not compatible with OpenMMTheory.run()")
            print("Constraints must instead be defined inside the program that called OpenMMtheory.run(), e.g. geomeTRICOptimizer.")
            if self.force_run is True:
                print("force_run is True. Will continue")
            else:
                ashexit()
        if defined_constraints != 0:
            print(BC.FAIL,"OpenMM constraints not zero. Exiting.",BC.END)
            if self.force_run is True:
                print("force_run is True. Will continue")
            else:
                ashexit()

        print_time_rel(timeA, modulename="OpenMMTheory.run: constraints checking", currprintlevel=self.printlevel, currthreshold=2)
        # Making sure coords is np array and not list-of-lists
        current_coords = np.array(current_coords)
        factor = -49614.752589207
        if self.printlevel > 1: print("Updating coordinates.")
        timeA = time.time()

        # NOTE: THIS IS STILL RATHER SLOW
        current_coords_nm = current_coords * 0.1  # converting from Angstrom to nm
        pos = [openmm.Vec3(current_coords_nm[i, 0], current_coords_nm[i, 1], current_coords_nm[i, 2]) for i in
               range(len(current_coords_nm))] * openmm.unit.nanometer
        print_time_rel(timeA, modulename="Creating pos array", currprintlevel=self.printlevel, currthreshold=2)
        timeA = time.time()
        # THIS IS THE SLOWEST PART. Probably nothing to be done
        simulation.context.setPositions(pos)

        print_time_rel(timeA, modulename="Updating MM positions", currprintlevel=self.printlevel, currthreshold=2)
        timeA = time.time()
        # While these distance constraints should not matter, applying them makes the energy function agree with
        # previous benchmarking for bonded and nonbonded
        # https://www.ncbi.nlm.nih.gov/pmc/articles/PMC5549999/
        # Using 1e-6 hardcoded value since how used in paper
        # NOTE: Weirdly, applyconstraints is True result in constraints for TIP3P disappearing
        if self.applyconstraints_in_run is True:
            if self.printlevel > 1: print("Applying constraints before calculating MM energy.")
            simulation.context.applyConstraints(1e-6)
            print_time_rel(timeA, modulename="context: apply constraints", currprintlevel=self.printlevel, currthreshold=1)
            timeA = time.time()

        if self.printlevel > 1:
            print("Calling OpenMM getState.")
        if Grad is True:
            state = simulation.context.getState(getEnergy=True, getForces=True)
            self.energy = state.getPotentialEnergy().value_in_unit(openmm.unit.kilojoule_per_mole) / ash.constants.hartokj
            self.gradient = np.array(state.getForces(asNumpy=True) / factor)
        else:
            state = simulation.context.getState(getEnergy=True, getForces=False)
            self.energy = state.getPotentialEnergy().value_in_unit(openmm.unit.kilojoule_per_mole) / ash.constants.hartokj
        
        print_time_rel(timeA, modulename="OpenMM getState", currprintlevel=self.printlevel, currthreshold=2)

        if self.printlevel > 1:
            print("OpenMM Energy:", self.energy, "Eh")
            print("OpenMM Energy:", self.energy * ash.constants.harkcal, "kcal/mol")

        # Do energy components or not. Can be turned off for e.g. MM MD simulation
        if self.do_energy_decomposition is True:
            self.printEnergyDecomposition(simulation)
        if self.printlevel > 1:
            print_line_with_subheader2("Ending OpenMM interface")
        print_time_rel(module_init_time, modulename="OpenMM run", moduleindex=2, currprintlevel=self.printlevel, currthreshold=1)
        if Grad is True:
            return self.energy, self.gradient
        else:
            return self.energy

    # Get list of charges from chosen force object (usually original nonbonded force object)
    def getatomcharges_old(self, force):
        import openmm
        chargelist = []
        for i in range(force.getNumParticles()):
            charge = force.getParticleParameters(i)[0]
            if isinstance(charge, openmm.unit.Quantity):
                charge = charge / openmm.unit.elementary_charge
                chargelist.append(charge)
        self.charges = chargelist
        return chargelist

    def getatomcharges(self):
        import openmm
        chargelist = []
        for force in self.system.getForces():
            if isinstance(force, openmm.NonbondedForce):
                for i in range(force.getNumParticles()):
                    charge = force.getParticleParameters(i)[0]
                    if isinstance(charge, openmm.unit.Quantity):
                        charge = charge / openmm.unit.elementary_charge
                        chargelist.append(charge)
                self.charges = chargelist
        return chargelist

    # Delete selected exceptions. Only for Coulomb.
    # Used to delete Coulomb interactions involving QM-QM and QM-MM atoms
    def delete_exceptions(self, atomlist):
        import openmm
        timeA = time.time()
        print("Deleting Coulombexceptions for atomlist:", atomlist)
        for force in self.system.getForces():
            if isinstance(force, openmm.NonbondedForce):
                for exc in range(force.getNumExceptions()):
                    # print(force.getExceptionParameters(exc))
                    # force.getExceptionParameters(exc)
                    p1, p2, chargeprod, sigmaij, epsilonij = force.getExceptionParameters(exc)
                    if p1 in atomlist or p2 in atomlist:
                        # print("p1: {} and p2: {}".format(p1,p2))
                        # print("chargeprod:", chargeprod)
                        # print("sigmaij:", sigmaij)
                        # print("epsilonij:", epsilonij)
                        chargeprod._value = 0.0
                        force.setExceptionParameters(exc, p1, p2, chargeprod, sigmaij, epsilonij)
                        # print("New:", force.getExceptionParameters(exc))
        #self.create_simulation()
        #self.update_simulation()
        print_time_rel(timeA, modulename="delete_exceptions")

    # # Function to
    # def zero_nonbondedforce(self, atomlist, zeroCoulomb=True, zeroLJ=True):
    #     timeA = time.time()
    #     print("Zero-ing nonbondedforce")

    #     def charge_sigma_epsilon(charge, sigma, epsilon):
    #         if zeroCoulomb is True:
    #             newcharge = charge
    #             newcharge._value = 0.0

    #         else:
    #             newcharge = charge
    #         if zeroLJ is True:
    #             newsigma = sigma
    #             newsigma._value = 0.0
    #             newepsilon = epsilon
    #             newepsilon._value = 0.0
    #         else:
    #             newsigma = sigma
    #             newepsilon = epsilon
    #         return [newcharge, newsigma, newepsilon]

    #     # Zero all nonbonding interactions for atomlist
    #     for force in self.system.getForces():
    #         if isinstance(force, openmm.NonbondedForce):
    #             # Setting single particle parameters
    #             for atomindex in atomlist:
    #                 oldcharge, oldsigma, oldepsilon = force.getParticleParameters(atomindex)
    #                 newpars = charge_sigma_epsilon(oldcharge, oldsigma, oldepsilon)
    #                 print(newpars)
    #                 force.setParticleParameters(atomindex, newpars[0], newpars[1], newpars[2])
    #             print("force.getNumExceptions() ", force.getNumExceptions())
    #             print("force.getNumExceptionParameterOffsets() ", force.getNumExceptionParameterOffsets())
    #             print("force.getNonbondedMethod():", force.getNonbondedMethod())
    #             print("force.getNumGlobalParameters() ", force.getNumGlobalParameters())
    #             # Now doing exceptions
    #             for exc in range(force.getNumExceptions()):
    #                 print(force.getExceptionParameters(exc))
    #                 force.getExceptionParameters(exc)
    #                 p1, p2, chargeprod, sigmaij, epsilonij = force.getExceptionParameters(exc)
    #                 # chargeprod._value=0.0
    #                 # sigmaij._value=0.0
    #                 # epsilonij._value=0.0
    #                 newpars2 = charge_sigma_epsilon(chargeprod, sigmaij, epsilonij)
    #                 force.setExceptionParameters(exc, p1, p2, newpars2[0], newpars2[1], newpars2[2])
    #                 # print("New:", force.getExceptionParameters(exc))
    #             # force.updateParametersInContext(self.simulation.context)
    #         elif isinstance(force, openmm.CustomNonbondedForce):
    #             print("customnonbondedforce not implemented")
    #             ashexit()
    #     #self.create_simulation()
    #     self.update_simulation()
    #     print_time_rel(timeA, modulename="zero_nonbondedforce")
    #     # self.create_simulation()

    # Updating charges in OpenMM object. Used to set QM charges to 0 for example
    # Taking list of atom-indices and list of charges (usually zero) and setting new charge
    # Note: Exceptions also needs to be dealt with (see delete_exceptions)
    def update_charges(self, atomlist, atomcharges):
        import openmm
        timeA = time.time()
        print("Updating charges in OpenMM object.")
        assert len(atomlist) == len(atomcharges)
        # newcharges = []
        # print("atomlist:", atomlist)
        for atomindex, newcharge in zip(atomlist, atomcharges):
            # Updating big chargelist of OpenMM object.
            # TODO: Is this actually used?
            self.charges[atomindex] = newcharge
            # print("atomindex: ", atomindex)
            # print("newcharge: ",newcharge)
            oldcharge, sigma, epsilon = self.nonbonded_force.getParticleParameters(atomindex)
            # Different depending on type of NonbondedForce
            if isinstance(self.nonbonded_force, openmm.CustomNonbondedForce):
                self.nonbonded_force.setParticleParameters(atomindex, [newcharge, sigma, epsilon])
                # bla1,bla2,bla3 = self.nonbonded_force.getParticleParameters(i)
                # print("bla1,bla2,bla3", bla1,bla2,bla3)
            elif isinstance(self.nonbonded_force, openmm.NonbondedForce):
                self.nonbonded_force.setParticleParameters(atomindex, newcharge, sigma, epsilon)
                # bla1,bla2,bla3 = self.nonbonded_force.getParticleParameters(atomindex)
                # print("bla1,bla2,bla3", bla1,bla2,bla3)

        # Instead of recreating simulation we can just update like this:
        print("Updating simulation object for modified Nonbonded force.")
        printdebug("self.nonbonded_force:", self.nonbonded_force)
        # Making sure that there still is a nonbonded force present in system (in case deleted)
        for i, force in enumerate(self.system.getForces()):
            printdebug("i is {} and force is {}".format(i, force))
            if isinstance(force, openmm.NonbondedForce):
                printdebug("here")
                #NOTE: Attempt at disabling
                #self.nonbonded_force.updateParametersInContext(self.simulation.context)
            if isinstance(force, openmm.CustomNonbondedForce):
                pass
                #self.nonbonded_force.updateParametersInContext(self.simulation.context)
        #self.create_simulation()
        #self.update_simulation()
        printdebug("done here")
        print_time_rel(timeA, modulename="update_charges")

    def modify_bonded_forces(self, atomlist):
        import openmm
        timeA = time.time()
        print("Modifying bonded forces.")
        print("")
        # This is typically used by QM/MM object to set bonded forces to zero for qmatoms (atomlist)
        # Mimicking: https://github.com/openmm/openmm/issues/2792

        numharmbondterms_removed = 0
        numharmangleterms_removed = 0
        numpertorsionterms_removed = 0
        numcustomtorsionterms_removed = 0
        numcmaptorsionterms_removed = 0
        # numcmmotionterms_removed = 0
        numcustombondterms_removed = 0

        for force in self.system.getForces():
            if isinstance(force, openmm.HarmonicBondForce):
                printdebug("HarmonicBonded force")
                printdebug("There are {} HarmonicBond terms defined.".format(force.getNumBonds()))
                printdebug("")
                # REVISIT: Neglecting QM-QM and sQM1-MM1 interactions. i.e if one atom in bond-pair is QM we neglect
                for i in range(force.getNumBonds()):
                    # print("i:", i)
                    p1, p2, length, k = force.getBondParameters(i)
                    # print("p1: {} p2: {} length: {} k: {}".format(p1,p2,length,k))
                    # or: delete QM-QM and QM-MM
                    # and: delete QM-QM

                    if self.delete_QM1_MM1_bonded is True:
                        exclude = (p1 in atomlist or p2 in atomlist)
                    else:
                        exclude = (p1 in atomlist and p2 in atomlist)
                    # print("exclude:", exclude)
                    if exclude is True:
                        printdebug("exclude True")
                        printdebug("atomlist:", atomlist)
                        printdebug("i:", i)
                        printdebug("Before p1: {} p2: {} length: {} k: {}".format(p1, p2, length, k))
                        force.setBondParameters(i, p1, p2, length, 0)
                        numharmbondterms_removed += 1
                        p1, p2, length, k = force.getBondParameters(i)
                        printdebug("After p1: {} p2: {} length: {} k: {}".format(p1, p2, length, k))
                        printdebug("")
                #NOTE: Attempt at disabling as maybe not needed
                #force.updateParametersInContext(self.simulation.context)
            elif isinstance(force, openmm.HarmonicAngleForce):
                printdebug("HarmonicAngle force")
                printdebug("There are {} HarmonicAngle terms defined.".format(force.getNumAngles()))
                for i in range(force.getNumAngles()):
                    p1, p2, p3, angle, k = force.getAngleParameters(i)
                    # Are angle-atoms in atomlist?
                    presence = [i in atomlist for i in [p1, p2, p3]]
                    # Excluding if 2 or 3 QM atoms. i.e. a QM2-QM1-MM1 or QM3-QM2-QM1 term
                    # Originally set to 2
                    if presence.count(True) >= 2:
                        printdebug("presence.count(True):", presence.count(True))
                        printdebug("exclude True")
                        printdebug("atomlist:", atomlist)
                        printdebug("i:", i)
                        printdebug("Before p1: {} p2: {} p3: {} angle: {} k: {}".format(p1, p2, p3, angle, k))
                        force.setAngleParameters(i, p1, p2, p3, angle, 0)
                        numharmangleterms_removed += 1
                        p1, p2, p3, angle, k = force.getAngleParameters(i)
                        printdebug("After p1: {} p2: {} p3: {} angle: {} k: {}".format(p1, p2, p3, angle, k))
                #NOTE: Attempt at disabling as maybe not needed
                #force.updateParametersInContext(self.simulation.context)
            elif isinstance(force, openmm.PeriodicTorsionForce):
                printdebug("PeriodicTorsionForce force")
                printdebug("There are {} PeriodicTorsionForce terms defined.".format(force.getNumTorsions()))
                for i in range(force.getNumTorsions()):
                    p1, p2, p3, p4, periodicity, phase, k = force.getTorsionParameters(i)
                    # Are torsion-atoms in atomlist?
                    presence = [i in atomlist for i in [p1, p2, p3, p4]]
                    # Excluding if 3 or 4 QM atoms. i.e. a QM3-QM2-QM1-MM1 or QM4-QM3-QM2-QM1 term
                    # print("Before p1: {} p2: {} p3: {} p4: {} periodicity: {} phase: {} k: {}".format(p1,p2,p3,p4,periodicity, phase,k))
                    # Originally set to 3
                    if presence.count(True) >= 3:
                        printdebug("Found torsion in QM-region")
                        printdebug("presence.count(True):", presence.count(True))
                        printdebug("exclude True")
                        printdebug("atomlist:", atomlist)
                        printdebug("i:", i)
                        printdebug(
                            "Before p1: {} p2: {} p3: {} p4: {} periodicity: {} phase: {} k: {}".format(p1, p2, p3, p4,
                                                                                                        periodicity,
                                                                                                        phase, k))
                        force.setTorsionParameters(i, p1, p2, p3, p4, periodicity, phase, 0)
                        numpertorsionterms_removed += 1
                        p1, p2, p3, p4, periodicity, phase, k = force.getTorsionParameters(i)
                        printdebug(
                            "After p1: {} p2: {} p3: {} p4: {} periodicity: {} phase: {} k: {}".format(p1, p2, p3, p4,
                                                                                                       periodicity, phase, k))
                #NOTE: Attempt at disabling as maybe not needed                                                                                       
                #force.updateParametersInContext(self.simulation.context)
            elif isinstance(force, openmm.CustomTorsionForce):
                printdebug("CustomTorsionForce force")
                printdebug("There are {} CustomTorsionForce terms defined.".format(force.getNumTorsions()))
                for i in range(force.getNumTorsions()):
                    p1, p2, p3, p4, pars = force.getTorsionParameters(i)
                    # Are torsion-atoms in atomlist?
                    presence = [i in atomlist for i in [p1, p2, p3, p4]]
                    # Excluding if 3 or 4 QM atoms. i.e. a QM3-QM2-QM1-MM1 or QM4-QM3-QM2-QM1 term
                    # print("Before p1: {} p2: {} p3: {} p4: {} pars {}".format(p1,p2,p3,p4,pars))
                    # print("pars:", pars)
                    if presence.count(True) >= 3:
                        printdebug("Found torsion in QM-region")
                        printdebug("presence.count(True):", presence.count(True))
                        printdebug("exclude True")
                        printdebug("atomlist:", atomlist)
                        printdebug("i:", i)
                        printdebug("Before p1: {} p2: {} p3: {} p4: {} pars {}".format(p1, p2, p3, p4, pars))
                        force.setTorsionParameters(i, p1, p2, p3, p4, (0.0, 0.0))
                        numcustomtorsionterms_removed += 1
                        p1, p2, p3, p4, pars = force.getTorsionParameters(i)
                        printdebug("After p1: {} p2: {} p3: {} p4: {} pars {}".format(p1, p2, p3, p4, pars))
                #NOTE: Attempt at disabling as maybe not needed
                #force.updateParametersInContext(self.simulation.context)
            elif isinstance(force, openmm.CMAPTorsionForce):
                printdebug("CMAPTorsionForce force")
                printdebug("There are {} CMAP terms defined.".format(force.getNumTorsions()))
                printdebug("There are {} CMAP maps defined".format(force.getNumMaps()))
                # print("Assuming no CMAP terms in QM-region. Continuing")
                # Note (RB). CMAP is between pairs of backbone dihedrals.
                # Not sure if we can delete the terms:
                # http://docs.openmm.org/latest/api-c++/generated/OpenMM.CMAPTorsionForce.html
                #  
                # print("Map num 0", force.getMapParameters(0))
                # print("Map num 1", force.getMapParameters(1))
                # print("Map num 2", force.getMapParameters(2))
                for i in range(force.getNumTorsions()):
                    jj, p1, p2, p3, p4, v1, v2, v3, v4 = force.getTorsionParameters(i)
                    # Are torsion-atoms in atomlist?
                    presence = [i in atomlist for i in [p1, p2, p3, p4, v1, v2, v3, v4]]
                    # NOTE: Not sure how to use count properly here when dealing with torsion atoms in QM-region
                    if presence.count(True) >= 4:
                        printdebug(
                            "jj: {} p1: {} p2: {} p3: {} p4: {}      v1: {} v2: {} v3: {} v4: {}".format(jj, p1, p2, p3,
                                                                                                         p4, v1, v2, v3,
                                                                                                         v4))
                        printdebug("presence:", presence)
                        printdebug("Found CMAP torsion partner in QM-region")
                        printdebug("Not deleting. To be revisited...")
                        # print("presence.count(True):", presence.count(True))
                        # print("exclude True")
                        # print("atomlist:", atomlist)
                        # print("i:", i)
                        # print("Before p1: {} p2: {} p3: {} p4: {} pars {}".format(p1,p2,p3,p4,pars))
                        # force.setTorsionParameters(i, p1, p2, p3, p4, (0.0,0.0))
                        # numcustomtorsionterms_removed+=1
                        # p1, p2, p3, p4, pars = force.getTorsionParameters(i)
                        # print("After p1: {} p2: {} p3: {} p4: {} pars {}".format(p1,p2,p3,p4,pars))
                # force.updateParametersInContext(self.simulation.context)

            elif isinstance(force, openmm.CustomBondForce):
                printdebug("CustomBondForce")
                printdebug("There are {} force terms defined.".format(force.getNumBonds()))
                # Neglecting QM1-MM1 interactions. i.e if one atom in bond-pair is QM we neglect
                for i in range(force.getNumBonds()):
                    #print("i:", i)
                    p1, p2, vars = force.getBondParameters(i)
                    #print("p1: {} p2: {}".format(p1,p2))
                    #print("vars:", vars)
                    exclude = (p1 in atomlist and p2 in atomlist)
                    #print("exclude:", exclude)
                    #print("-----")
                    if exclude is True:
                        #print("exclude True")
                        #print("atomlist:", atomlist)
                        #print("i:", i)
                        #print("Before")
                        #print("p1: {} p2: {}")
                        #force.setBondParameters(i, p1, p2, [0.0, 0.0, 0.0])
                        #NOTE: list of parameters now set to 0.0 for any number of parameters
                        force.setBondParameters(i, p1, p2, [0.0 for i in vars])
                        numcustombondterms_removed += 1
                        p1, p2, vars = force.getBondParameters(i)
                        #print("After:")
                        #print("p1: {} p2: {}")
                        #print("vars:", vars)
                        # ashexit()
                #NOTE: Attempt at disabling as maybe not needed
                #force.updateParametersInContext(self.simulation.context)

            elif isinstance(force, openmm.CMMotionRemover):
                pass
                # print("CMMotionRemover ")
                # print("nothing to be done")
            elif isinstance(force, openmm.CustomNonbondedForce):
                pass
                # print("CustomNonbondedForce force")
                # print("nothing to be done")
            elif isinstance(force, openmm.NonbondedForce):
                pass
                # print("NonbondedForce force")
                # print("nothing to be done")
            else:
                pass
                # print("Other force: ", force)
                # print("nothing to be done")

        print("")
        print("Number of bonded terms removed:", )
        print("Harmonic Bond terms:", numharmbondterms_removed)
        print("Harmonic Angle terms:", numharmangleterms_removed)
        print("Periodic Torsion terms:", numpertorsionterms_removed)
        print("Custom Torsion terms:", numcustomtorsionterms_removed)
        print("CMAP Torsion terms:", numcmaptorsionterms_removed)
        print("CustomBond terms", numcustombondterms_removed)
        print("")
        #self.create_simulation()
        #self.update_simulation()
        print_time_rel(timeA, modulename="modify_bonded_forces")


# For frozen systems we use Customforce in order to specify interaction groups
# if len(self.frozen_atoms) > 0:

# Two possible ways.
# https://github.com/openmm/openmm/issues/2698
# 1. Use CustomNonbondedForce  with interaction groups. Could be slow
# 2. CustomNonbondedForce but with scaling


# https://ahy3nz.github.io/posts/2019/30/openmm2/
# http://www.maccallumlab.org/news/2015/1/23/testing

# Comes close to NonbondedForce results (after exclusions) but still not correct
# The issue is most likely that the 1-4 LJ interactions should not be excluded but rather scaled.
# See https://github.com/openmm/openmm/issues/1200
# https://github.com/openmm/openmm/issues/1696
# How to do:
# 1. Keep nonbonded force for only those interactions and maybe also electrostatics?
# Mimic this??: https://github.com/openmm/openmm/blob/master/devtools/forcefield-scripts/processCharmmForceField.py
# Or do it via Parmed? Better supported for future??
# 2. Go through the 1-4 interactions and not exclude but scale somehow manually. But maybe we can't do that in
# CustomNonbonded Force?
# Presumably not but maybe can add a special force object just for 1-4 interactions. We
def create_cnb(original_nbforce):
    import openmm
    """Creates a CustomNonbondedForce object that mimics the original nonbonded force
    and also a Custombondforce to handle 14 exceptions
    """
    # Next, create a CustomNonbondedForce with LJ and Coulomb terms
    ONE_4PI_EPS0 = 138.935456
    # ONE_4PI_EPS0=1.0
    # TODO: Not sure whether sqrt should be present or not in epsilon???
    energy_expression = "4*epsilon*((sigma/r)^12 - (sigma/r)^6) + ONE_4PI_EPS0*chargeprod/r;"
    # sqrt ??
    energy_expression += "epsilon = sqrt(epsilon1*epsilon2);"
    energy_expression += "sigma = 0.5*(sigma1+sigma2);"
    energy_expression += "ONE_4PI_EPS0 = {:f};".format(ONE_4PI_EPS0)  # already in OpenMM units
    energy_expression += "chargeprod = charge1*charge2;"
    custom_nonbonded_force = openmm.CustomNonbondedForce(energy_expression)
    custom_nonbonded_force.addPerParticleParameter('charge')
    custom_nonbonded_force.addPerParticleParameter('sigma')
    custom_nonbonded_force.addPerParticleParameter('epsilon')
    # Configure force
    custom_nonbonded_force.setNonbondedMethod(openmm.CustomNonbondedForce.NoCutoff)
    # custom_nonbonded_force.setCutoffDistance(9999999999)
    custom_nonbonded_force.setUseLongRangeCorrection(False)
    # custom_nonbonded_force.setUseSwitchingFunction(True)
    # custom_nonbonded_force.setSwitchingDistance(99999)
    print('Adding particles to custom force.')
    for index in range(self.system.getNumParticles()):
        [charge, sigma, epsilon] = original_nbforce.getParticleParameters(index)
        custom_nonbonded_force.addParticle([charge, sigma, epsilon])
    # For CustomNonbondedForce we need (unlike NonbondedForce) to create exclusions that correspond to the automatic
    # exceptions in NonbondedForce
    # These are interactions that are skipped for bonded atoms
    numexceptions = original_nbforce.getNumExceptions()
    print("numexceptions in original_nbforce: ", numexceptions)

    # Turn exceptions from NonbondedForce into exclusions in CustombondedForce
    # except 1-4 which are not zeroed but are scaled. These are added to Custombondforce
    exceptions_14 = []
    numexclusions = 0
    for i in range(0, numexceptions):
        # print("i:", i)
        # Get exception parameters (indices)
        p1, p2, charge, sigma, epsilon = original_nbforce.getExceptionParameters(i)
        # print("p1,p2,charge,sigma,epsilon:", p1,p2,charge,sigma,epsilon)
        # If 0.0 then these are CHARMM 1-2 and 1-3 interactions set to zero
        if charge._value == 0.0 and epsilon._value == 0.0:
            # print("Charge and epsilons are 0.0. Add proper exclusion")
            # Set corresponding exclusion in customnonbforce
            custom_nonbonded_force.addExclusion(p1, p2)
            numexclusions += 1
        else:
            # print("This is not an exclusion but a scaled interaction as it is is non-zero. Need to keep")
            exceptions_14.append([p1, p2, charge, sigma, epsilon])
            # [798, 801, Quantity(value=-0.0684, unit=elementary charge**2), Quantity(value=0.2708332103146632, unit=nanometer), Quantity(value=0.2672524882578271, unit=kilojoule/mole)]

    print("len exceptions_14", len(exceptions_14))
    # print("exceptions_14:", exceptions_14)
    print("numexclusions:", numexclusions)

    # Creating custombondforce to handle these special exceptions
    # Now defining pair parameters
    # https://github.com/openmm/openmm/issues/2698
    energy_expression = "(4*epsilon*((sigma/r)^12 - (sigma/r)^6) + ONE_4PI_EPS0*chargeprod/r);"
    energy_expression += "ONE_4PI_EPS0 = {:f};".format(ONE_4PI_EPS0)  # already in OpenMM units
    custom_bond_force = openmm.CustomBondForce(energy_expression)
    custom_bond_force.addPerBondParameter('chargeprod')
    custom_bond_force.addPerBondParameter('sigma')
    custom_bond_force.addPerBondParameter('epsilon')

    for exception in exceptions_14:
        idx = exception[0]
        jdx = exception[1]
        c = exception[2]
        sig = exception[3]
        eps = exception[4]
        custom_bond_force.addBond(idx, jdx, [c, sig, eps])

    print('Number of defined 14 bonds in custom_bond_force:', custom_bond_force.getNumBonds())

    return custom_nonbonded_force, custom_bond_force


# TODO: Look into: https://github.com/ParmEd/ParmEd/blob/7e411fd03c7db6977e450c2461e065004adab471/parmed/structure.py#L2554

# myCustomNBForce= simtk.openmm.CustomNonbondedForce("4*epsilon*((sigma/r)^12-(sigma/r)^6); sigma=0.5*(sigma1+sigma2); epsilon=sqrt(epsilon1*epsilon2)")
# myCustomNBForce.setNonbondedMethod(simtk.openmm.app.NoCutoff)
# myCustomNBForce.setCutoffDistance(1000*simtk.openmm.unit.angstroms)
# Frozen-Act interaction
# myCustomNBForce.addInteractionGroup(self.frozen_atoms,self.active_atoms)
# Act-Act interaction
# myCustomNBForce.addInteractionGroup(self.active_atoms,self.active_atoms)


# Clean up list of lists of constraint definition. Add distance if missing
def clean_up_constraints_list(fragment=None, constraints=None):
    print("Checking defined constraints.")
    newconstraints = []
    for con in constraints:
        if len(con) == 3:
            newconstraints.append(con)
        elif len(con) == 2:
            distance = distance_between_atoms(fragment=fragment, atom1=con[0], atom2=con[1])
            print("Adding missing distance definition between atoms {} and {}: {:.4f}".format(con[0], con[1], distance))
            newcon = [con[0], con[1], distance]
            newconstraints.append(newcon)
    return newconstraints


def OpenMM_Opt(fragment=None, theory=None, maxiter=1000, tolerance=1, enforcePeriodicBox=True):
    import openmm
    module_init_time = time.time()
    print_line_with_mainheader("OpenMM Optimization")

    if fragment is None:
        print("No fragment object. Exiting.")
        ashexit()

    # Distinguish between OpenMM theory or QM/MM theory
    if isinstance(theory, OpenMMTheory):
        openmmobject = theory
    else:
        print("Only OpenMMTheory allowed in OpenMM_Opt. Exiting.")
        ashexit()

    print("Number of atoms:", fragment.numatoms)
    print("Max iterations:", maxiter)
    print("Energy tolerance:", tolerance)

    print("OpenMM autoconstraints:", openmmobject.autoconstraints)
    print("OpenMM hydrogenmass:", openmmobject.hydrogenmass)
    print("OpenMM rigidwater constraints:", openmmobject.rigidwater)

    if openmmobject.user_constraints:
        print("User constraints:", openmmobject.user_constraints)
    else:
        print("User constraints: None")

    if openmmobject.user_restraints:
        print("User restraints:", openmmobject.user_restraints)
    else:
        print("User restraints: None")
    print("Number of frozen atoms:", len(openmmobject.user_frozen_atoms))
    if 0 < len(openmmobject.user_frozen_atoms) < 50:
        print("Frozen atoms", openmmobject.user_frozen_atoms)
    print("")

    if openmmobject.autoconstraints is None:
        print(f"{BC.WARNING}WARNING: Autoconstraints have not been set in OpenMMTheory object definition.{BC.END}")
        print(f"{BC.WARNING}This means that by default no bonds are constrained in the optimization.{BC.END}")
        print("Will continue...")
    if openmmobject.rigidwater is True and len(openmmobject.user_frozen_atoms) != 0 or (
            openmmobject.autoconstraints is not None and len(openmmobject.user_frozen_atoms) != 0):
        print(
            f"{BC.WARNING}WARNING: Frozen_atoms options selected but there are general constraints defined in{BC.END} "
            f"{BC.WARNING}the OpenMM object (either rigidwater=True or autoconstraints is not None)\n{BC.END}"
            f"{BC.WARNING}OpenMM will crash if constraints and frozen atoms involve the same atoms{BC.END}")


    openmmobject.set_simulation_parameters(timestep=0.001, temperature=1, integrator='VerletIntegrator')

    #CREATE SIMULATION OBJECT
    simulation = openmmobject.create_simulation()
    print("Simulation created.")

    # Context: settings positions of simulation object
    print("Now adding coordinates")
    openmmobject.set_positions(fragment.coords,simulation)

    print("")
    state = simulation.context.getState(getEnergy=True, getForces=True,
                                                     enforcePeriodicBox=enforcePeriodicBox)
    print("Initial potential energy is: {} Eh".format(
        state.getPotentialEnergy().value_in_unit_system(openmm.unit.md_unit_system) / ash.constants.hartokj))
    kjmolnm_to_atomic_factor = -49614.752589207
    forces_init = np.array(state.getForces(asNumpy=True)) / kjmolnm_to_atomic_factor
    rms_force = np.sqrt(sum(n * n for n in forces_init.flatten()) / len(forces_init.flatten()))
    print("RMS force: {} Eh/Bohr".format(rms_force))
    print("Max force component: {} Eh/Bohr".format(forces_init.max()))
    print("")
    print("Starting minimization.")

    simulation.minimizeEnergy(maxIterations=maxiter, tolerance=tolerance)
    print("Minimization done.")
    print("")
    state = simulation.context.getState(getEnergy=True, getPositions=True, getForces=True,
                                                     enforcePeriodicBox=enforcePeriodicBox)
    print("Potential energy is: {} Eh".format(
        state.getPotentialEnergy().value_in_unit_system(openmm.unit.md_unit_system) / ash.constants.hartokj))
    forces_final = np.array(state.getForces(asNumpy=True)) / kjmolnm_to_atomic_factor
    rms_force = np.sqrt(sum(n * n for n in forces_final.flatten()) / len(forces_final.flatten()))
    print("RMS force: {} Eh/Bohr".format(rms_force))
    print("Max force component: {} Eh/Bohr".format(forces_final.max()))

    # Get coordinates
    newcoords = state.getPositions(asNumpy=True).value_in_unit(openmm.unit.angstrom)
    print("")
    print("Updating coordinates in ASH fragment.")
    fragment.coords = newcoords

    with open('frag-minimized.pdb', 'w') as f:
        openmm.app.pdbfile.PDBFile.writeHeader(openmmobject.topology, f)
    with open('frag-minimized.pdb', 'a') as f:
        openmm.app.pdbfile.PDBFile.writeModel(openmmobject.topology,
                                                           simulation.context.getState(getPositions=True,
                                                                                                    enforcePeriodicBox=enforcePeriodicBox).getPositions(),
                                                           f)

    print('All Done!')
    print_time_rel(module_init_time, modulename="OpenMM_Opt", moduleindex=1)


def OpenMM_Modeller(pdbfile=None, forcefield=None, xmlfile=None, waterxmlfile=None, watermodel=None, pH=7.0,
                    solvent_padding=10.0, solvent_boxdims=None, extraxmlfile=None, residue_variants=None,
                    ionicstrength=0.1, pos_iontype='Na+', neg_iontype='Cl-', use_higher_occupancy=False,
                    platform="CPU"):
    module_init_time = time.time()
    print_line_with_mainheader("OpenMM Modeller")
    try:
        import openmm as openmm
        import openmm.app as openmm_app
        import openmm.unit as openmm_unit
        print("Imported OpenMM library version:", openmm.__version__)

    except ImportError:
        raise ImportError(
            "OpenMM requires installing the OpenMM package. Try: 'conda install -c conda-forge openmm'  \
            Also see http://docs.openmm.org/latest/userguide/application.html")
    try:
        import pdbfixer
    except ImportError:
        print("Problem importing pdbfixer. Install first via conda:")
        print("conda install -c conda-forge pdbfixer")
        ashexit()

    if pdbfile == None:
        print("You must provide a pdbfile= keyword argument")
        ashexit()

    def write_pdbfile_openMM(topology, positions, filename):
        openmm.app.PDBFile.writeFile(topology, positions, file=open(filename, 'w'))
        print("Wrote PDB-file:", filename)

    def print_systemsize():
        print("System size: {} atoms\n".format(len(modeller.getPositions())))

    # https://github.com/openmm/openmm/wiki/Frequently-Asked-Questions#template


    if residue_variants == None:
        residue_variants={}


    # Water model. May be overridden by forcefield below
    if watermodel == "tip3p":
        # Possible Problem: this only has water, no ions.
        waterxmlfile = "tip3p.xml"
        modeller_solvent_name="tip3p" #Used when adding solvent
    elif watermodel == "tip3p_charmm":
        waterxmlfile = "charmm36/water.xml"
        modeller_solvent_name="tip3p" #Used when adding solvent
    elif waterxmlfile is not None:
        # Problem: we need to define watermodel also
        print("Using waterxmlfile:", waterxmlfile)
    # Forcefield options
    if forcefield is not None:
        if forcefield == 'Amber99':
            xmlfile = "amber99sb.xml"
        elif forcefield == 'Amber96':
            xmlfile = "amber96.xml"
        elif forcefield == 'Amber03':
            xmlfile = "amber03.xml"
        elif forcefield == 'Amber10':
            xmlfile = "amber10.xml"
        elif forcefield == 'Amber14':
            xmlfile = "amber14-all.xml"
            # Using specific Amber FB version of TIP3P
            if watermodel == "tip3p":
                modeller_solvent_name="tip3p" #Used when adding solvent
                waterxmlfile = "amber14/tip3pfb.xml"
        elif forcefield == 'Amber96':
            xmlfile = "amber96.xml"
        elif forcefield == 'CHARMM36':
            xmlfile = "charmm36.xml"
            # Using specific CHARMM36 version of TIP3P
            watermodel="tip3p"
            modeller_solvent_name="tip3p" #Used when adding solvent
            waterxmlfile = "charmm36/water.xml"
        elif forcefield == 'CHARMM2013':
            xmlfile = "charmm_polar_2013.xml"
        elif forcefield == 'Amoeba2013':
            xmlfile = "amoeba2013.xml"
        elif forcefield == 'Amoeba2009':
            xmlfile = "amoeba2009.xml"
    elif xmlfile is not None:
        print("Using xmlfile:", xmlfile)
    else:
        print("You must provide a forcefield or xmlfile keyword!")
        ashexit()

    print("PDBfile:", pdbfile)
    print("Forcefield:", forcefield)
    print("XMfile:", xmlfile)
    print("Water model:", watermodel)
    print("Water xmlfile:", waterxmlfile)
    print("pH:", pH)

    print("User-provided dictionary of residue_variants:", residue_variants)
    #Basic checks
    if extraxmlfile is not None:
        print("Using extra XML file:", extraxmlfile)
        #Checking if file exists first before continuing
        if os.path.isfile(extraxmlfile) is not True:
            print(BC.FAIL,"File {} can not be found. Exiting.".format(extraxmlfile),BC.END)
            ashexit()
    if xmlfile is None:
        print("xmlfile is none. Something went wrong. Exiting")
        ashexit()

    ############
    # Define a forcefield based on defined xml-files
    if extraxmlfile is None and waterxmlfile is None:
        forcefield = openmm_app.forcefield.ForceField(xmlfile)
    elif extraxmlfile is not None and waterxmlfile is None:
        forcefield = openmm_app.forcefield.ForceField(xmlfile,extraxmlfile)
    elif extraxmlfile is None and waterxmlfile is not None:
        forcefield = openmm_app.forcefield.ForceField(xmlfile,waterxmlfile)
    elif extraxmlfile is not None and waterxmlfile is not None:
        forcefield = openmm_app.forcefield.ForceField(xmlfile,extraxmlfile,waterxmlfile)


    print("\nNow checking PDB-file for alternate locations, i.e. multiple occupancies:\n")

    
    #Check PDB-file whether it contains alternate locations of residue atoms (multiple occupations)
    #Default behaviour: 
    # - if no multiple occupancies return input PDBfile and go on
    # - if multiple occupancies, print list of residues and tell user to fix them. Exiting
    # - if use_higher_occupancy is set to True, user higher occupancy location, write new PDB_file and use
    pdbfile=find_alternate_locations_residues(pdbfile, use_higher_occupancy=use_higher_occupancy)

    print("Using PDB-file", pdbfile)

    # Fix basic mistakes in PDB by PDBFixer
    # This will e.g. fix bad terminii
    print("\nRunning PDBFixer")
    fixer = pdbfixer.PDBFixer(pdbfile)
    fixer.findMissingResidues()
    print("Found missing residues:", fixer.missingResidues)
    fixer.findNonstandardResidues()
    print("Found non-standard residues:", fixer.nonstandardResidues)
    # fixer.replaceNonstandardResidues()
    fixer.findMissingAtoms()
    print("Found missing atoms:", fixer.missingAtoms)
    print("Found missing terminals:", fixer.missingTerminals)
    fixer.addMissingAtoms()
    print("Added missing atoms.")
    #exit()

    openmm_app.PDBFile.writeFile(fixer.topology, fixer.positions, open('system_afterfixes.pdb', 'w'))
    print("PDBFixer done.")
    print(BC.WARNING,"Warning: PDBFixer can create unreasonable orientations of residues if residues are missing or multiple occupancies are present.\n \
    You should inspect the created PDB-file to be sure.",BC.END)
    print("Wrote PDBfile: system_afterfixes.pdb")

    # Load fixed PDB-file and create Modeller object
    pdb = openmm_app.PDBFile("system_afterfixes.pdb")
    print("\n\nNow loading Modeller.")
    modeller = openmm_app.Modeller(pdb.topology, pdb.positions)
    modeller_numatoms = modeller.topology.getNumAtoms()
    numresidues = modeller.topology.getNumResidues()
    numchains = modeller.topology.getNumChains()
    modeller_atoms=list(modeller.topology.atoms())
    modeller_bonds=list(modeller.topology.bonds())
    modeller_chains=list(modeller.topology.chains())
    modeller_residues=list(modeller.topology.residues())
    print("Modeller topology has {} residues.".format(numresidues))
    print("Modeller topology has {} chains.".format(numchains))
    print("Modeller topology has {} atoms.".format(modeller_numatoms))
    print("Chains:", modeller_chains)
    #Getting residues for each chain
    for chain_x in modeller_chains:
        print("This is chain {}, it has {} residues and they are: {}\n".format(chain_x.index,len(chain_x._residues),chain_x._residues))
    print("\n")

    #PRINTING big table of residues
    print("User defined residue variants per chain:")
    for rv_key,rv_vals in residue_variants.items():
        print("Chain {} : {}".format(rv_key,rv_vals))
    print("\nMODELLER TOPOLOGY - RESIDUES TABLE\n")
    print("  {:<12}{:<13}{:<13}{:<13}{:<13}       {}".format("ASH-resid","Resname","Chain-index", "Chain-name", "ResID-in-chain","User-modification"))
    print("-"*100)
    current_chainindex=0
    #Also using loop to get residue_states list that we pass on to modeller.addHydrogens
    residue_states=[]
    for each_residue in modeller_residues:
        #Division line between chains
        if each_residue.chain.index != current_chainindex:
            print("--"*30)
        resid=each_residue.index
        resid_in_chain=int(each_residue.id)
        resname=each_residue.name
        chain=each_residue.chain
        current_chainindex=each_residue.chain.index
        if chain.id in residue_variants:
            if resid_in_chain in residue_variants[chain.id]:
                residue_states.append(residue_variants[chain.id][resid_in_chain])
                FLAGLABEL="-- This residue will be changed to: {} --".format(residue_variants[chain.id][resid_in_chain])
            else:
                residue_states.append(None) #Note: we add None since we don't want to influence addHydrogens 
                FLAGLABEL=""
        else:
            residue_states.append(None)  #Note: we add None since we don't want to influence addHydrogens
            FLAGLABEL=""

        print("  {:<12}{:<13}{:<13}{:<13}{:<13}       {}".format(resid,resname,chain.index,chain.id, resid_in_chain,FLAGLABEL))

    openmm_app.PDBFile.writeFile(modeller.topology, modeller.positions, open('system_afterfixes2.pdb', 'w'))


    #NOTE: to be deleted
    if len(residue_states) != numresidues:
        print("residue_states != numresidues. Something went wrong")
        ashexit()

    # Adding hydrogens feeding in residue_states
    # This is were missing residue/atom errors will come
    print("")
    print("Adding hydrogens for pH:", pH)
    #print("Providing full list of residue_states", residue_states)
    print("Warning: OpenMM Modeller will fail in this step if residue information is missing")
    try:
        modeller.addHydrogens(forcefield, pH=pH, variants=residue_states)
    except ValueError as errormessage:
        print(BC.FAIL,"\nError: OpenMM modeller.addHydrogens signalled a ValueError",BC.END)
        print("This is a common error and suggests a problem in PDB-file or missing residue information in the forcefield.")
        print("Non-standard inorganic/organic residues require providing an additional XML-file via extraxmlfile= option")
        print("Note that C-terminii require the dangling O-atom to be named OXT ")
        print("Read the ASH documentation or the OpenMM documentation on dealing with this problem.")
        print("\nFull error message from OpenMM:")
        print(errormessage)
        print()
        ashexit()

    write_pdbfile_openMM(modeller.topology, modeller.positions, "system_afterH.pdb")
    print_systemsize()

    # Adding Solvent
    print("Adding solvent, modeller_solvent_name:", modeller_solvent_name)
    if solvent_boxdims is not None:
        print("Solvent boxdimension provided: {} Å".format(solvent_boxdims))
        modeller.addSolvent(forcefield, neutralize=False, boxSize=openmm.Vec3(solvent_boxdims[0], solvent_boxdims[1],
                                                            solvent_boxdims[2]) * openmm_unit.angstrom)
    else:
        print("Using solvent padding (solvent_padding=X keyword): {} Å".format(solvent_padding))
        modeller.addSolvent(forcefield, neutralize=False, padding=solvent_padding * openmm_unit.angstrom, model=modeller_solvent_name)
    write_pdbfile_openMM(modeller.topology, modeller.positions, "system_aftersolvent.pdb")
    print_systemsize()

    # Ions
    print("Adding ionic strength: {} M, using ions: {} and {}".format(ionicstrength, pos_iontype, neg_iontype))
    modeller.addSolvent(forcefield, neutralize=True, positiveIon=pos_iontype, negativeIon=neg_iontype, 
        ionicStrength=ionicstrength * openmm_unit.molar)
    write_pdbfile_openMM(modeller.topology, modeller.positions, "system_afterions.pdb")
    write_pdbfile_openMM(modeller.topology, modeller.positions, "finalsystem.pdb")
    print_systemsize()

    # Create ASH fragment and write to disk
    fragment = Fragment(pdbfile="system_afterions.pdb")
    fragment.print_system(filename="finalsystem.ygg")
    fragment.write_xyzfile(xyzfilename="finalsystem.xyz")

    print("\nOpenMM_Modeller used the following XML-files to define system:")
    print("General forcefield XML file:", xmlfile)
    print("Solvent forcefield XML file:", waterxmlfile)
    print("Extra forcefield XML file:", extraxmlfile)

    #Creating new OpenMM object from forcefield so that we can write out system XMLfile
    print("Creating OpenMMTheory object")
    openmmobject =OpenMMTheory(platform=platform, forcefield=forcefield, topoforce=True,
                        topology=modeller.topology, pdbfile=None, periodic=True,
                        autoconstraints='HBonds', rigidwater=True, printlevel=0)
    #Write out System XMLfile
    #TODO: Disable ?
    systemxmlfile="system_full.xml"

    serialized_system = openmm.XmlSerializer.serialize(openmmobject.system)
    with open(systemxmlfile, 'w') as f:
        f.write(serialized_system)
    
    print("\n\nFiles written to disk:")
    print("system_afteratlocfixes.pdb")
    print("system_afterfixes.pdb")
    print("system_afterfixes2.pdb")
    print("system_afterH.pdb")
    print("system_aftersolvent.pdb")
    print("system_afterions.pdb and finalsystem.pdb (same)")
    print("\nFinal files:")
    print("finalsystem.pdb  (PDB file)")
    print("finalsystem.ygg  (ASH fragment file)")
    print("finalsystem.xyz   (XYZ coordinate file)")
    print("{}   (System XML file)".format(systemxmlfile))
    print(BC.OKGREEN,"\n\n OpenMM_Modeller done! System has been fully set up!\n",BC.END)
    print(BC.WARNING,"Strongly recommended: Check finalsystem.pdb carefully for correctness!", BC.END)
    print("\nTo use this system setup to define a future OpenMMTheory object you can either do:\n")

    print(BC.OKMAGENTA,"1. Define using separate forcefield XML files:",BC.END)
    if extraxmlfile is None:
        print("omm = OpenMMTheory(xmlfiles=[\"{}\", \"{}\"], pdbfile=\"finalsystem.pdb\", periodic=True)".format(xmlfile,waterxmlfile),BC.END)
    else:
        print("omm = OpenMMTheory(xmlfiles=[\"{}\", \"{}\", \"{}\"], pdbfile=\"finalsystem.pdb\", periodic=True)".format(xmlfile,waterxmlfile,extraxmlfile),BC.END)

    print(BC.OKMAGENTA,"2. Use full system XML-file (USUALLY NOT RECOMMENDED ):\n",BC.END, \
        "omm = OpenMMTheory(xmlsystemfile=\"system_full.xml\", pdbfile=\"finalsystem.pdb\", periodic=True)\n",BC.END)
    print()
    print()
    #Check system for atoms with large gradient and print warning
    #TODO: Can we avoid re-creating the omm object ?
    print("Now running single-point MM job to check for bad contacts")
    omm =OpenMMTheory(platform=platform, forcefield=forcefield, topoforce=True,
                        topology=modeller.topology, pdbfile=None, periodic=True,
                        autoconstraints=None, rigidwater=False, printlevel=0)
    SP_result = Singlepoint(theory=omm, fragment=fragment, Grad=True)
    check_gradient_for_bad_atoms(fragment=fragment,gradient=SP_result.gradient, threshold=45000)
    
    print_time_rel(module_init_time, modulename="OpenMM_Modeller", moduleindex=1)
    
    #Return openmmobject. Could be used directly
    return openmmobject, fragment




# Assumes all atoms present (including hydrogens)
def solvate_small_molecule(fragment=None, charge=None, mult=None, watermodel=None, solvent_boxdims=[70.0, 70.0, 70.0],
                           nonbonded_pars="CM5_UFF", orcatheory=None, numcores=1):
    # , ionicstrength=0.1, iontype='K+'
    print_line_with_mainheader("SmallMolecule Solvator")
    try:
        import openmm as openmm
        import openmm.app as openmm_app
        import openmm.unit as openmm_unit
        from openmm import XmlSerializer
        print("Imported OpenMM library version:", openmm.__version__)

    except ImportError:
        raise ImportError(
            "OpenMM requires installing the OpenMM package. Try: conda install -c conda-forge openmm  \
            Also see http://docs.openmm.org/latest/userguide/application.html")

    def write_pdbfile_openMM(topology, positions, filename):
        openmm.app.PDBFile.writeFile(topology, positions, file=open(filename, 'w'))
        print("Wrote PDB-file:", filename)

    def print_systemsize():
        print("System size: {} atoms\n".format(len(modeller.getPositions())))

    # Defining simple atomnames and atomtypes to be used for solute
    atomnames = [el + "Y" + str(i) for i, el in enumerate(fragment.elems)]
    atomtypes = [el + "X" + str(i) for i, el in enumerate(fragment.elems)]

    # Take input ASH fragment and write a basic PDB file via ASH
    write_pdbfile(fragment, outputname="smallmol", dummyname='LIG', atomnames=atomnames)

    # Load PDB-file and create Modeller object
    pdb = openmm_app.PDBFile("smallmol.pdb")
    print("Loading Modeller.")
    modeller = openmm_app.Modeller(pdb.topology, pdb.positions)
    numresidues = modeller.topology.getNumResidues()
    print("Modeller topology has {} residues.".format(numresidues))

    # Forcefield

    # TODO: generalize to other solvents.
    # Create local ASH library of XML files
    if watermodel == "tip3p" or watermodel == "TIP3P" :
        print("Using watermodel=TIP3P . Using parameters in:", ashpath + "/databases/forcefields")
        forcefieldpath = ashpath + "/databases/forcefields"
        waterxmlfile = forcefieldpath + "/tip3p_water_ions.xml"
        coulomb14scale = 1.0
        lj14scale = 1.0
    elif watermodel == "charmm_tip3p":
        coulomb14scale = 1.0
        lj14scale = 1.0
        # NOTE: Problem combining this and solute XML file.
        print("Using watermodel: CHARMM-TIP3P (has ion parameters also)")
        # This is the modified CHARMM-TIP3P (LJ parameters on H at least, maybe bonded parameters defined also)
        # Advantage: also contains ion parameters
        waterxmlfile = "charmm36/water.xml"
    else:
        print("Unknown watermodel.")
        ashexit()

    # Define nonbonded paramers
    if nonbonded_pars == "CM5_UFF":
        print("Using CM5 atomcharges and UFF-LJ parameters.")
        atompropdict = basic_atom_charges_ORCA(fragment=fragment, charge=charge, mult=mult,
                                               orcatheory=orcatheory, chargemodel="CM5", numcores=numcores)
        charges = atompropdict['charges']
        # Basic UFF LJ parameters
        # Converting r0 parameters from Ang to nm and to sigma
        sigmas = [UFF_modH_dict[el][0] * 0.1 / (2 ** (1 / 6)) for el in fragment.elems]
        # Convering epsilon from kcal/mol to kJ/mol
        epsilons = [UFF_modH_dict[el][1] * 4.184 for el in fragment.elems]
    elif nonbonded_pars == "DDEC3" or nonbonded_pars == "DDEC6":
        print("Using {} atomcharges and DDEC-derived parameters.".format(nonbonded_pars))
        atompropdict = basic_atom_charges_ORCA(fragment=fragment, charge=charge, mult=mult,
                                               orcatheory=orcatheory, chargemodel=nonbonded_pars, numcores=numcores)
        charges = atompropdict['charges']
        r0 = atompropdict['r0s']
        eps = atompropdict['epsilons']
        sigmas = [s * 0.1 / (2 ** (1 / 6)) for s in r0]
        epsilons = [e * 4.184 for e in eps]
    elif nonbonded_pars == "xtb_UFF":
        print("Using xTB charges and UFF-LJ parameters.")
        charges = basic_atomcharges_xTB(fragment=fragment, charge=charge, mult=mult, xtbmethod='GFN2')
        # Basic UFF LJ parameters
        # Converting r0 parameters from Ang to nm and to sigma
        sigmas = [UFF_modH_dict[el][0] * 0.1 / (2 ** (1 / 6)) for el in fragment.elems]
        # Convering epsilon from kcal/mol to kJ/mol
        epsilons = [UFF_modH_dict[el][1] * 4.184 for el in fragment.elems]
    else:
        print("Unknown nonbonded_pars option.")
        ashexit()

    print("sigmas:", sigmas)
    print("epsilons:", epsilons)

    # Creating XML-file for solute

    xmlfile = write_xmlfile_nonbonded(resnames=["LIG"], atomnames_per_res=[atomnames], atomtypes_per_res=[atomtypes],
                                      elements_per_res=[fragment.elems], masses_per_res=[fragment.masses],
                                      charges_per_res=[charges],
                                      sigmas_per_res=[sigmas], epsilons_per_res=[epsilons], filename="solute.xml",
                                      coulomb14scale=coulomb14scale, lj14scale=lj14scale)

    print("Creating forcefield using XML-files:", xmlfile, waterxmlfile)
    forcefield = openmm_app.forcefield.ForceField(*[xmlfile, waterxmlfile])

    # , waterxmlfile
    # if extraxmlfile == None:
    #    print("here")
    #    forcefield=openmm_app.forcefield.ForceField(xmlfile, waterxmlfile)
    # else:
    #    print("Using extra XML file:", extraxmlfile)
    #    forcefield=openmm_app.forcefield.ForceField(xmlfile, waterxmlfile, extraxmlfile)

    # Solvent+Ions
    print("Adding solvent, watermodel:", watermodel)
    # NOTE: modeller.addsolvent will automatically add ions to neutralize any excess charge
    # TODO: Replace with something simpler
    if solvent_boxdims is not None:
        print("Solvent boxdimension provided: {} Å".format(solvent_boxdims))
        modeller.addSolvent(forcefield, boxSize=openmm.Vec3(solvent_boxdims[0], solvent_boxdims[1],
                                                            solvent_boxdims[2]) * openmm_unit.angstrom)

    # Write out solvated system coordinates
    print("Creating PDB-file: system_aftersolvent.pdb")
    write_pdbfile_openMM(modeller.topology, modeller.positions, "system_aftersolvent.pdb")
    print_systemsize()
    # Create ASH fragment and write to disk
    newfragment = Fragment(pdbfile="system_aftersolvent.pdb")
    newfragment.print_system(filename="newfragment.ygg")
    newfragment.write_xyzfile(xyzfilename="newfragment.xyz")
    print("Creating XYZ-file: newfragment.xyz")
    print()
    print("\nTo use this system setup to define a future OpenMMTheory object you can  do:\n")

    print(f"omm = OpenMMTheory(xmlfiles=[\"{xmlfile}\", \"{waterxmlfile}\"], pdbfile=\"system_aftersolvent.pdb\", periodic=True, rigidwater=True)",BC.END)
    print()
    print()

    # Return forcefield object,  topology object and ASH fragment
    return forcefield, modeller.topology, newfragment


# Simple XML-writing function. Will only write nonbonded parameters
def write_xmlfile_nonbonded(resnames=None, atomnames_per_res=None, atomtypes_per_res=None, elements_per_res=None,
                            masses_per_res=None, charges_per_res=None, sigmas_per_res=None,
                            epsilons_per_res=None, filename="system.xml", coulomb14scale=0.833333, 
                            lj14scale=0.5, skip_nb=False, charmm=False):
    print("Inside write_xml file")
    # resnames=["MOL1", "MOL2"]
    # atomnames_per_res=[["CM1","CM2","HX1","HX2"],["OT1","HT1","HT2"]]
    # atomtypes_per_res=[["CM","CM","H","H"],["OT","HT","HT"]]
    # sigmas_per_res=[[1.2,1.2,1.3,1.3],[1.25,1.17,1.17]]
    # epsilons_per_res=[[0.2,0.2,0.3,0.3],[0.25,0.17,0.17]]
    # etc.
    # Always list of lists now

    assert len(resnames) == len(atomnames_per_res) == len(atomtypes_per_res)
    # Get list of all unique atomtypes, elements, masses
    # all_atomtypes=list(set([item for sublist in atomtypes_per_res for item in sublist]))
    # all_elements=list(set([item for sublist in elements_per_res for item in sublist]))
    # all_masses=list(set([item for sublist in masses_per_res for item in sublist]))

    # Create list of all AtomTypelines (unique)
    atomtypelines = []
    for resname, atomtypelist, elemlist, masslist in zip(resnames, atomtypes_per_res, elements_per_res, masses_per_res):
        for atype, elem, mass in zip(atomtypelist, elemlist, masslist):
            atomtypeline = "<Type name=\"{}\" class=\"{}\" element=\"{}\" mass=\"{}\"/>\n".format(atype, atype, elem,
                                                                                                  str(mass))
            if atomtypeline not in atomtypelines:
                atomtypelines.append(atomtypeline)
    # Create list of all nonbonded lines (unique)
    nonbondedlines = []
    LJforcelines = []
    for resname, atomtypelist, chargelist, sigmalist, epsilonlist in zip(resnames, atomtypes_per_res, charges_per_res,
                                                                         sigmas_per_res, epsilons_per_res):
        print("atomtypelist:", atomtypelist)
        print("chargelist.", chargelist)
        print("sigmalist", sigmalist)
        for atype, charge, sigma, epsilon in zip(atomtypelist, chargelist, sigmalist, epsilonlist):
            if charmm == True:
                #LJ parameters zero here
                nonbondedline = "<Atom type=\"{}\" charge=\"{}\" sigma=\"{}\" epsilon=\"{}\"/>\n".format(atype, charge,0.0, 0.0)
                #Here we set LJ parameters
                ljline = "<Atom type=\"{}\" sigma=\"{}\" epsilon=\"{}\"/>\n".format(atype, sigma, epsilon) 
                if nonbondedline not in nonbondedlines:
                    nonbondedlines.append(nonbondedline)
                if ljline not in LJforcelines:
                    LJforcelines.append(ljline)
            else:
                nonbondedline = "<Atom type=\"{}\" charge=\"{}\" sigma=\"{}\" epsilon=\"{}\"/>\n".format(atype, charge,
                                                                                                        sigma, epsilon)
                if nonbondedline not in nonbondedlines:
                    nonbondedlines.append(nonbondedline)

    with open(filename, 'w') as xmlfile:
        xmlfile.write("<ForceField>\n")
        xmlfile.write("<AtomTypes>\n")
        for atomtypeline in atomtypelines:
            xmlfile.write(atomtypeline)
        xmlfile.write("</AtomTypes>\n")
        xmlfile.write("<Residues>\n")
        for resname, atomnamelist, atomtypelist in zip(resnames, atomnames_per_res, atomtypes_per_res):
            xmlfile.write("<Residue name=\"{}\">\n".format(resname))
            for i, (atomname, atomtype) in enumerate(zip(atomnamelist, atomtypelist)):
                xmlfile.write("<Atom name=\"{}\" type=\"{}\"/>\n".format(atomname, atomtype))
            # All other atoms
            xmlfile.write("</Residue>\n")
        xmlfile.write("</Residues>\n")
        if skip_nb is False:

            if charmm == True:
                #Writing both Nonbnded force block and also LennardJonesForce block
                xmlfile.write("<NonbondedForce coulomb14scale=\"{}\" lj14scale=\"{}\">\n".format(coulomb14scale, lj14scale))
                for nonbondedline in nonbondedlines:
                    xmlfile.write(nonbondedline)
                xmlfile.write("</NonbondedForce>\n")
                xmlfile.write("<LennardJonesForce lj14scale=\"{}\">\n".format(lj14scale))
                for ljline in LJforcelines:
                    xmlfile.write(ljline)
                xmlfile.write("</LennardJonesForce>\n")
            else:
                #Only NonbondedForce block
                xmlfile.write("<NonbondedForce coulomb14scale=\"{}\" lj14scale=\"{}\">\n".format(coulomb14scale, lj14scale))
                for nonbondedline in nonbondedlines:
                    xmlfile.write(nonbondedline)
                xmlfile.write("</NonbondedForce>\n")
        xmlfile.write("</ForceField>\n")
    print("Wrote XML-file:", filename)
    return filename


# TODO: Move elsewhere?
def basic_atomcharges_xTB(fragment=None, charge=None, mult=None, xtbmethod='GFN2'):
    print("Now calculating atom charges for fragment.")
    print("Using default xTB charges.")
    calc = xTBTheory(runmode='inputfile',xtbmethod=xtbmethod)

    Singlepoint(theory=calc, fragment=fragment, charge=charge, mult=mult)
    atomcharges = grabatomcharges_xTB()
    print("atomcharges:", atomcharges)
    print("fragment elems:", fragment.elems)
    return atomcharges


# TODO: Move elsewhere?
def basic_atom_charges_ORCA(fragment=None, charge=None, mult=None, orcatheory=None, chargemodel=None, numcores=1):
    atompropdict = {}
    print("Will calculate charges using ORCA.")

    # Define default ORCA object if notprovided
    if orcatheory is None:
        print("orcatheory not provided. Will do r2SCAN/def2-SVP single-point calculation")
        orcasimpleinput = "! r2SCAN def2-SVP tightscf "
        orcablocks = "%scf maxiter 300 end"
        orcatheory = ORCATheory(orcasimpleinput=orcasimpleinput,
                                orcablocks=orcablocks, numcores=numcores)
    if chargemodel == 'CM5':
        orcatheory.extraline = chargemodel_select(chargemodel)
    # Run ORCA calculation
    Singlepoint(theory=orcatheory, fragment=fragment, charge=charge, mult=mult)
    if 'DDEC' not in chargemodel:
        atomcharges = grabatomcharges_ORCA(chargemodel, orcatheory.filename + '.out')
        atompropdict['charges'] = atomcharges
    else:
        atomcharges, molmoms, voldict = DDEC_calc(elems=fragment.elems, theory=orcatheory,
                                                  gbwfile=orcatheory.filename + '.gbw', numcores=numcores,
                                                  DDECmodel='DDEC3', calcdir='DDEC', molecule_charge=charge,
                                                  molecule_spinmult=mult)
        atompropdict['charges'] = atomcharges
        r0list, epsilonlist = DDEC_to_LJparameters(fragment.elems, molmoms, voldict)
        print("r0list:", r0list)
        print("epsilonlist:", epsilonlist)
        atompropdict['r0s'] = r0list
        atompropdict['epsilons'] = epsilonlist

    print("atomcharges:", atomcharges)
    print("fragment elems:", fragment.elems)
    return atompropdict


def read_NPT_statefile(npt_output):
    import csv
    from collections import defaultdict
    # Read in CSV file of last NPT simulation and store in lists
    columns = defaultdict(list)

    with open(npt_output, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            for (k, v) in row.items():
                columns[k].append(v)
    # Extract step number, volume and density and cast as floats
    steps = np.array(columns['#"Step"'])
    volume = np.array(columns["Box Volume (nm^3)"]).astype(float)
    density = np.array(columns["Density (g/mL)"]).astype(float)

    resultdict = {"steps": steps, "volume": volume, "density": density}
    return resultdict

###########################
# CLASS-BASED OpenMM_MD
###########################


# Wrapper function for OpenMM_MDclass
def OpenMM_MD(fragment=None, theory=None, timestep=0.004, simulation_steps=None, simulation_time=None,
              traj_frequency=1000, temperature=300, integrator='LangevinMiddleIntegrator',
              barostat=None, pressure=1, trajectory_file_option='DCD', trajfilename='trajectory',
              coupling_frequency=1, charge=None, mult=None, printlevel=2, hydrogenmass=1.5,
              anderson_thermostat=False, platform='CPU', constraints=None,
              enforcePeriodicBox=True, dummyatomrestraint=False, center_on_atoms=None, solute_indices=None,
              datafilename=None, dummy_MM=False, plumed_object=None, add_center_force=False,
              center_force_atoms=None, centerforce_constant=1.0, barostat_frequency=25, specialbox=False):
    print_line_with_mainheader("OpenMM MD wrapper function")
    md = OpenMM_MDclass(fragment=fragment, theory=theory, charge=charge, mult=mult, timestep=timestep,
                        traj_frequency=traj_frequency, temperature=temperature, integrator=integrator,
                        barostat=barostat, pressure=pressure, trajectory_file_option=trajectory_file_option, constraints=constraints,
                        coupling_frequency=coupling_frequency, anderson_thermostat=anderson_thermostat, platform=platform,
                        enforcePeriodicBox=enforcePeriodicBox, dummyatomrestraint=dummyatomrestraint, center_on_atoms=center_on_atoms, solute_indices=solute_indices,
                        datafilename=datafilename, dummy_MM=dummy_MM, printlevel=printlevel, hydrogenmass=hydrogenmass,
                        plumed_object=plumed_object, add_center_force=add_center_force,trajfilename=trajfilename,
                        center_force_atoms=center_force_atoms, centerforce_constant=centerforce_constant,
                        barostat_frequency=barostat_frequency, specialbox=specialbox)
    if simulation_steps is not None:
        md.run(simulation_steps=simulation_steps)
    elif simulation_time is not None:
        md.run(simulation_time=simulation_time)
    else:
        print("Either simulation_steps or simulation_time need to be defined (not both).")
        ashexit()


class OpenMM_MDclass:
    def __init__(self, fragment=None, theory=None, charge=None, mult=None, timestep=0.004,
                 traj_frequency=1000, temperature=300, integrator='LangevinMiddleIntegrator',
                 barostat=None, pressure=1, trajectory_file_option='DCD', trajfilename='trajectory',
                 coupling_frequency=1, printlevel=2, platform='CPU',
                 anderson_thermostat=False, hydrogenmass=1.5, constraints=None,
                 enforcePeriodicBox=True, dummyatomrestraint=False, center_on_atoms=None, solute_indices=None,
                 datafilename=None, dummy_MM=False, plumed_object=None, add_center_force=False,
                 center_force_atoms=None, centerforce_constant=1.0,
                 barostat_frequency=25, specialbox=False,):
        module_init_time = time.time()
        import openmm
        print_line_with_mainheader("OpenMM Molecular Dynamics Initialization")

        if fragment is None:
            print("No fragment object. Exiting.")
            ashexit()
        else:
            self.fragment = fragment

        #Check charge/mult
        self.charge, self.mult = check_charge_mult(charge, mult, theory.theorytype, fragment, "OpenMM_MD", theory=theory)

        #External QM option off by default
        self.externalqm=False

        #Trajectory filename. Used for trajs in DCD, PDB etc. format, also single PDB snapshots
        self.trajfilename=trajfilename

        # Distinguish between OpenMM theory QM/MM theory or QM theory
        self.dummy_MM=dummy_MM

        #Printlevel
        self.printlevel=printlevel

        #Case: OpenMMTheory
        if isinstance(theory, OpenMMTheory):
            self.openmmobject = theory
            self.QM_MM_object = None
        #Case: QM/MM theory with OpenMM mm_theory
        elif isinstance(theory, ash.QMMMTheory):
            self.QM_MM_object = theory
            self.openmmobject = theory.mm_theory
        #Case: OpenMM with external QM
        else:
            #NOTE: Recognize QM theories here ??
            print("Unrecognized theory.")
            print("Will assume to be QM theory and will continue")
            print("QM-program forces will be added as a custom external force to OpenMM")
            self.externalqm=True
            print("Now creating OpenMMTheory object")
            print("OpenMM platform:", platform)
            #Creating dummy OpenMMTheory (basic topology, particle masses, no forces except CMMRemoval)
            self.openmmobject = OpenMMTheory(fragment=fragment, dummysystem=True, platform=platform, printlevel=printlevel,
                                hydrogenmass=hydrogenmass, constraints=constraints) #NOTE: might add more options here
            self.QM_MM_object = None
            self.qmtheory=theory
        
        # Assigning some basic variables
        self.temperature = temperature
        self.pressure = pressure
        self.integrator = integrator
        self.coupling_frequency = coupling_frequency
        self.timestep = timestep
        self.traj_frequency = int(traj_frequency)
        self.plumed_object = plumed_object
        self.barostat_frequency = barostat_frequency
        self.trajectory_file_option=trajectory_file_option
        #PERIODIC or not
        if self.openmmobject.Periodic is True:
            #Generally we want True but for now allowing user to modify (default=True)
            self.enforcePeriodicBox=enforcePeriodicBox
        else:
            print("System is non-periodic. Setting enforcePeriodicBox to False")
            #Non-periodic. Setting enforcePeriodicBox to False (otherwise nonsense)
            self.enforcePeriodicBox=False
        
        print_line_with_subheader2("MD system parameters")
        print("Temperature: {} K".format(self.temperature))
        print("OpenMM autoconstraints:", self.openmmobject.autoconstraints)
        print("OpenMM hydrogenmass:",
               self.openmmobject.hydrogenmass)  # Note 1.5 amu mass is recommended for LangevinMiddle with 4fs timestep
        print("OpenMM rigidwater constraints:", self.openmmobject.rigidwater)
        print("User Constraints:", self.openmmobject.user_constraints)
        print("User Restraints:", self.openmmobject.user_restraints)
        print("Number of atoms:", self.fragment.numatoms)
        print("Number of frozen atoms:", len(self.openmmobject.user_frozen_atoms))
        if len(self.openmmobject.user_frozen_atoms) < 50:
             print("Frozen atoms", self.openmmobject.user_frozen_atoms)
        print("Integrator:", self.integrator)
        print("Timestep: {} ps".format(self.timestep))
        print("Anderon Thermostat:", anderson_thermostat)
        print("coupling_frequency: {} ps^-1 (for Nose-Hoover and Langevin integrators)".format(self.coupling_frequency))
        print("Barostat:", barostat)

        print("")
        print("Will write trajectory in format:", self.trajectory_file_option)
        print("Trajectory write frequency:", self.traj_frequency)
        print("enforcePeriodicBox:", self.enforcePeriodicBox)
        print("")
        #specialbox for QM/MM
        self.specialbox=specialbox

        if self.openmmobject.autoconstraints is None:
            print(f"""{BC.WARNING}
                WARNING: Autoconstraints have not been set in OpenMMTheory object definition. This means that by 
                         default no bonds are constrained in the MD simulation. This usually requires a small 
                         timestep: 0.5 fs or so.
                         autoconstraints='HBonds' is recommended for 2 fs timesteps with LangevinIntegrator and 4fs with LangevinMiddleIntegrator).
                         autoconstraints='AllBonds' or autoconstraints='HAngles' allows even larger timesteps to be used.
                         See : https://github.com/openmm/openmm/pull/2754 and https://github.com/openmm/openmm/issues/2520 
                         for recommended simulation settings in OpenMM.
                         {BC.END}""")
            print("Will continue...")
        if self.openmmobject.rigidwater is True and len(self.openmmobject.user_frozen_atoms) != 0 or (
                self.openmmobject.autoconstraints is not None and len(self.openmmobject.user_frozen_atoms) != 0):
            print(
                f"{BC.WARNING}WARNING: Frozen_atoms options selected but there are general constraints defined in{BC.END} "
                f"{BC.WARNING}the OpenMM object (either rigidwater=True or autoconstraints is not None){BC.END}"
                f"{BC.WARNING}\nOpenMM will crash if constraints and frozen atoms involve the same atoms{BC.END}")
        print("")

        print("Defining atom positions from fragment")
        #Note: using self.positions as we may add dummy atoms (e.g. dummyatomrestraint below) 
        self.positions = self.fragment.coords
        
        #Dummy-atom restraint to deal with NPT simulations that contain constraints/restraints/frozen_atoms
        self.dummyatomrestraint=dummyatomrestraint
        if self.dummyatomrestraint is True:
            if solute_indices == None:
                print("Dummyatomrestraint requires solute_indices to be set")
                ashexit()
            print(BC.WARNING,"Warning: Using dummyatomrestraints. This means that we will add a dummy atom to topology and OpenMM coordinates")
            print("We do not add the dummy atom to ASH-fragment")
            print("Affects visualization of trajectory (make sure to use PDB-file that contains the dummy-atom, printed in the end)",BC.END)
            #Should be centroid of solute or something rather
            solute_coords = np.take(self.fragment.coords, solute_indices, axis=0)
            dummypos=get_centroid(solute_coords)
            print("Dummy atom will be added to position:", dummypos)
            #Adding dummy-atom coordinates to self.positions
            self.positions = np.append(self.positions, [dummypos], axis=0)
            print("len self.pos", len(self.positions))
            print("len self.fragment.coords", len(self.fragment.coords))

            #Restraining solute atoms to dummy-atom
            self.openmmobject.add_dummy_atom_to_restrain_solute(atomindices=solute_indices)

        #TRANSLATE solute: #https://github.com/openmm/openmm/issues/1854
        # Translate solute to geometric center on origin
        #centroid = np.mean(positions[solute, :] / positions.unit, axis=0) * positions.unit
        #positions -= centroid            
        if center_on_atoms != None:
            solute_coords = np.take(self.fragment.coords, solute_indices, axis=0)
            changed_origin_coords = change_origin_to_centroid(self.fragment.coords, subsetcoords=solute_coords)
            print("changed_origin_coords", changed_origin_coords)

        forceclassnames = [i.__class__.__name__ for i in self.openmmobject.system.getForces()]
        # Set up system with chosen barostat, thermostat, integrator
        if barostat is not None:
            print("Attempting to add barostat.")
            if "MonteCarloBarostat" not in forceclassnames:
                print("Adding barostat.")
                montecarlobarostat=openmm.MonteCarloBarostat(self.pressure * openmm.unit.bar,
                                                                self.temperature * openmm.unit.kelvin)
                #Setting barostat frequency to chosen value or default (25)
                montecarlobarostat.setFrequency(self.barostat_frequency)
                self.openmmobject.system.addForce(montecarlobarostat)
            else:
                print("Barostat already present. Skipping.")
            # print("after barostat added")

            self.integrator = "LangevinMiddleIntegrator"
            print("Barostat requires using integrator:", integrator)
            self.openmmobject.set_simulation_parameters(timestep=self.timestep, temperature=self.temperature, 
                                                        integrator=self.integrator, coupling_frequency=self.coupling_frequency)
        elif anderson_thermostat is True:
            print("Anderson thermostat is on.")
            if "AndersenThermostat" not in forceclassnames:
                self.openmmobject.system.addForce(
                    openmm.AndersenThermostat(self.temperature * openmm.unit.kelvin,
                                                                1 / openmm.unit.picosecond))
            self.integrator = "VerletIntegrator"
            print("Now using integrator:", integrator)
            self.openmmobject.set_simulation_parameters(timestep=self.timestep, temperature=self.temperature, 
                                                        integrator=self.integrator, coupling_frequency=self.coupling_frequency)
        else:
            # Deleting barostat and Andersen thermostat if present from previous sims
            for i, forcename in enumerate(forceclassnames):
                if forcename == "MonteCarloBarostat" or forcename == "AndersenThermostat":
                    print("Removing old force:", forcename)
                    self.openmmobject.system.removeForce(i)

            # Regular thermostat or integrator without barostat
            # Integrators: LangevinIntegrator, LangevinMiddleIntegrator, NoseHooverIntegrator, VerletIntegrator,
            # BrownianIntegrator, VariableLangevinIntegrator, VariableVerletIntegrator
            self.openmmobject.set_simulation_parameters(timestep=self.timestep, temperature=self.temperature, 
                                                        integrator=self.integrator, coupling_frequency=self.coupling_frequency)

        if barostat is not None:
            self.volume = self.density = True
        else:
            self.volume = self.density = False

        # If statedatareporter filename set:
        self.datafilename=datafilename
        if self.datafilename is not None:
            #Remove old file
            #Added because of problems (19 May 2023 by CVS) in read NPT data file (OpenMM box relaxation) as header is printed each time
            #Now removing file before starting. Possibly better to put this elsewhere as we may sometimes
            # want to keep running simulation while appending to datafile
            try:
                os.remove(self.datafilename)
            except FileNotFoundError:
                pass

            #Now doing open file object in append mode instead of just filename.
            #Just filename does not play nice when running simulation step by step
            #Future OpenMM update may do this automatically?
            self.dataoutputoption = open(self.datafilename,'a')
            print("Will write data to file:", self.datafilename)
        # otherwise stdout:
        else:
            self.dataoutputoption = stdout

        # NOTE: Better to use OpenMM-plumed interface instead??
        if plumed_object is not None:
            print("Plumed active")
            # Create new OpenMM custom external force
            print("Creating new OpenMM custom external force for Plumed.")
            self.plumedcustomforce = self.openmmobject.add_custom_external_force()

        # QM/MM MD
        if self.QM_MM_object is not None:
            print("QM_MM_object provided. Switching to QM/MM loop.")
            #print("QM/MM requires enforcePeriodicBox to be False.")
            #True means we end up with solute in corner of box (wrong for nonPBC QM code)
            #NOTE: but OK for proteins?
            #self.enforcePeriodicBox = True
            # enforcePeriodicBox or not
            print("self.enforcePeriodicBox:", self.enforcePeriodicBox)

            # OpenMM_MD with QM/MM object does not make sense without openmm_externalforce
            # (it would calculate OpenMM energy twice) so turning on in case forgotten
            if self.QM_MM_object.openmm_externalforce is False:
                print("QM/MM object was not set to have 'openmm_externalforce=True'.")
                print("Turning on externalforce option.")
                self.QM_MM_object.openmm_externalforce = True
                #NOTE: Now creating externalforceobject as part of this MD object instead (previously QM/MM object)
                self.openmm_externalforceobject = self.openmmobject.add_custom_external_force()
            # TODO: Should we set parallelization of QM theory here also in case forgotten?

            centercoordinates = False
            # CENTER COORDINATES HERE on SOLUTE HERE ??
            # TODO: Deprecated I think
            if centercoordinates is True:
                # Solute atoms assumed to be QM-region
                self.fragment.write_xyzfile(xyzfilename="fragment-before-centering.xyz")
                soluteatoms = self.QM_MM_object.qmatoms
                solutecoords = self.fragment.get_coords_for_atoms(soluteatoms)[0]
                print("Changing origin to centroid.")
                self.fragment.coords = change_origin_to_centroid(fragment.coords, subsetcoords=solutecoords)
                self.fragment.write_xyzfile(xyzfilename="fragment-after-centering.xyz")

            # Now adding center force acting on solute
            if add_center_force is True:
                # print("add_center_force is True")
                print("Forceconstant is: {} kcal/mol/Ang^2".format(centerforce_constant))
                if center_force_atoms is None:
                    print("center_force_atoms unset. Using QM/MM atoms:", self.QM_MM_object.qmatoms)
                    center_force_atoms = self.QM_MM_object.qmatoms
                # Get geometric center of system (Angstrom)
                center = self.fragment.get_coordinate_center()
                print("center:", center)

                self.openmmobject.add_center_force(center_coords=center, atomindices=center_force_atoms,
                                                   forceconstant=centerforce_constant)

            # After adding possible QM/MM force, possible Plumed force, possible center force
            # Let's list all OpenMM object system forces for sanity
            print("OpenMM Forces defined:", self.openmmobject.system.getForces())
            # Does step by step

            print_time_rel(module_init_time, modulename="OpenMM_MD setup", moduleindex=1)

    #Set sim reporters. Needs to be done after simulation is created and not modified anymore
    def set_sim_reporters(self,simulation):
        import openmm
        #StateDataReporter
        self.statedatareporter=openmm.app.StateDataReporter(self.dataoutputoption, self.traj_frequency, step=True, time=True,
                                                           potentialEnergy=True, kineticEnergy=True, volume=self.volume,
                                                           density=self.density, temperature=True, separator=',')
        simulation.reporters.append(self.statedatareporter)

        #TODO: See if this can be made to work for simulations with step-by-step
        if self.trajectory_file_option == 'PDB':
            simulation.reporters.append(
                openmm.app.PDBReporter(self.trajfilename+'.pdb', self.traj_frequency,
                                                         enforcePeriodicBox=self.enforcePeriodicBox))
        elif self.trajectory_file_option == 'DCD':
            # NOTE: Disabling for now
            # with open('initial_MDfrag_step1.pdb', 'w') as f: openmm.app.pdbfile.PDBFile
            # .writeModel(openmmobject.topology, self.simulation.context.getState(getPositions=True,
            # enforcePeriodicBox=enforcePeriodicBox).getPositions(), f)
            # print("Wrote PDB")
            simulation.reporters.append(
                openmm.app.DCDReporter(self.trajfilename+'.dcd', self.traj_frequency,
                                                         enforcePeriodicBox=self.enforcePeriodicBox))
        elif self.trajectory_file_option == 'NetCDFReporter':
            print("NetCDFReporter traj format selected. This requires mdtraj. Importing.")
            mdtraj = MDtraj_import()
            simulation.reporters.append(
                mdtraj.reporters.NetCDFReporter(self.trajfilename+'.nc', self.traj_frequency))
        elif self.trajectory_file_option == 'HDF5Reporter':
            print("HDF5Reporter traj format selected. This requires mdtraj. Importing.")
            mdtraj = MDtraj_import()
            simulation.reporters.append(
                mdtraj.reporters.HDF5Reporter(self.trajfilename+'.lh5', self.traj_frequency,
                                              enforcePeriodicBox=self.enforcePeriodicBox))

    # Simulation loop.
    #NOTE: process_id passed by Simple_parallel function when doing multiprocessing, e.g. Plumed multiwalker metadynamics
    def run(self, simulation_steps=None, simulation_time=None, metadynamics=False, metadyn_settings=None, 
            plumedinput=None, process_id=None, workerdir=None, restraints=None):
        module_init_time = time.time()
        print_line_with_mainheader("OpenMM Molecular Dynamics Run")
        import openmm
        if simulation_steps is None and simulation_time is None:
            print("Either simulation_steps or simulation_time needs to be set.")
            ashexit()
        if simulation_time is not None:
            simulation_steps = int(simulation_time / self.timestep)
        if simulation_steps is not None:
            simulation_time = simulation_steps * self.timestep

        ##################################
        # CREATE SIMULATION OBJECT
        ##################################

        #Parallelization handling
        if process_id == None:
            process_id=0
        if workerdir != None:
            print(f"Workerdir: {workerdir} provided. Entering dir")
            os.chdir(workerdir)

        #If using Plumed then now we add Plumed-force to system from plumedinput string
        if plumedinput != None:
            import openmmplumed
            print("Plumed active. Adding Plumedforce to system")
            if process_id != None:
                print(f"process_id ({process_id}) passed to md.run. Assuming multiwalker Plumed MD run")
                print("plumedinput:", plumedinput)
                plumedinput=plumedinput.replace("WALKERID",str(process_id))
                print("plumedinput:", plumedinput)
                writestringtofile(plumedinput,"plumedinput.in")
            self.openmmobject.system.addForce(openmmplumed.PlumedForce(plumedinput))

        #Case native OpenMM metadynamcis
        if metadynamics is True:

            biasdir=metadyn_settings["biasdir"]
            try:
                os.remove("colvar")
            except:
                pass
            #Reference positions for RMSD. Currently limited to starting position
            if metadyn_settings["CV1_type"] == 'rmsd' or metadyn_settings["CV2_type"] == 'rmsd':
                coords_nm = self.fragment.coords * 0.1  # converting from Angstrom to nm
                reference_pos = [openmm.Vec3(coords_nm[i, 0], coords_nm[i, 1], coords_nm[i, 2]) for i in
                    range(len(coords_nm))] * openmm.unit.nanometer
            else:
                reference_pos=None
            #Creating meta_object from settings provided
            if metadyn_settings["numCVs"] == 2:
                #Creating CV biasvariables and forces
                CV1_bias,cvforce_1 = create_CV_bias(metadyn_settings["CV1_type"],metadyn_settings["CV1_atoms"],metadyn_settings["CV1_biaswidth"],
                                                    CV_range=metadyn_settings["CV1_range"], reference_pos=reference_pos, reference_particles=metadyn_settings["CV1_atoms"])
                CV2_bias,cvforce_2 = create_CV_bias(metadyn_settings["CV2_type"],metadyn_settings["CV2_atoms"],metadyn_settings["CV2_biaswidth"],
                                                    CV_range=metadyn_settings["CV2_range"], reference_pos=reference_pos, reference_particles=metadyn_settings["CV2_atoms"])

                #Gridwidth and min/max values now set. Adding to dict
                metadyn_settings["CV1_gridwidth"] = CV1_bias.gridWidth
                metadyn_settings["CV2_gridwidth"] = CV2_bias.gridWidth
                metadyn_settings["CV1_minvalue"] = CV1_bias.minValue
                metadyn_settings["CV1_maxvalue"] = CV1_bias.maxValue
                metadyn_settings["CV2_minvalue"] = CV2_bias.minValue
                metadyn_settings["CV2_maxvalue"] = CV2_bias.maxValue
                ##Possible flatbottom or other restraint accompanying CV
                if metadyn_settings["flatbottom_restraint_CV1"] != None:
                    print("Adding flatbottom restraint for CV1")
                    self.openmmobject.add_CV_restraint(cvforce_1, metadyn_settings["flatbottom_restraint_CV1"],metadyn_settings["CV2_type"])
                if metadyn_settings["flatbottom_restraint_CV2"] != None:
                    print("Adding flatbottom restraint for CV2")
                    self.openmmobject.add_CV_restraint(cvforce_2, metadyn_settings["flatbottom_restraint_CV2"],metadyn_settings["CV2_type"])

                meta_object = openmm.app.Metadynamics(self.openmmobject.system, [CV1_bias,CV2_bias], metadyn_settings["temperature"], 
                                                            metadyn_settings["biasfactor"], metadyn_settings["height"], metadyn_settings["frequency"],
                                                            saveFrequency=metadyn_settings["saveFrequency"], biasDir=metadyn_settings["biasdir"])
            elif metadyn_settings["numCVs"] == 1:
                #Creating CV biasvariable and force
                CV1_bias,cvforce_1 = create_CV_bias(metadyn_settings["CV1_type"],metadyn_settings["CV1_atoms"],metadyn_settings["CV1_biaswidth"],
                                                    CV_range=metadyn_settings["CV1_range"], reference_pos=reference_pos, reference_particles=metadyn_settings["CV1_atoms"])
                #Gridwidth and min/max values now set. Adding to dict
                metadyn_settings["CV1_gridwidth"] = CV1_bias.gridWidth
                metadyn_settings["CV1_minvalue"] = CV1_bias.minValue
                metadyn_settings["CV1_maxvalue"] = CV1_bias.maxValue
                metadyn_settings["CV2_gridwidth"] = None
                ##Possible flatbottom or other restraint accompanying CV
                if metadyn_settings["flatbottom_restraint_CV1"] != None:
                    print("Adding flatbottom restraint for CV1")
                    self.openmmobject.add_CV_restraint(cvforce_1, metadyn_settings["flatbottom_restraint_CV1"],metadyn_settings["CV1_type"])

                meta_object = openmm.app.Metadynamics(self.openmmobject.system, [CV1_bias], metadyn_settings["temperature"], 
                                                            metadyn_settings["biasfactor"], metadyn_settings["height"], metadyn_settings["frequency"],
                                                            saveFrequency=metadyn_settings["saveFrequency"], biasDir=metadyn_settings["biasdir"])

            #Writing metadyn_settings dict to disk
            import json
            json.dump(metadyn_settings, open(f"{biasdir}/ASH_MTD_parameters.txt",'w'))
        #Case: QM MD
        if self.externalqm is True:
            print("Creating new OpenMM custom external force for external QM theory.")
            self.qmcustomforce = self.openmmobject.add_custom_external_force()

        #Possible restraints added
        if restraints != None:
            print("Adding restraints")
            self.openmmobject.add_bondrestraints(restraints=restraints)


        #Creating simulation object
        simulation = self.openmmobject.create_simulation()
        print("Simulation created.")
        print(self.openmmobject.integrator)
        forceclassnames = [i.__class__.__name__ for i in self.openmmobject.system.getForces()]

        ##################################
        # PRINT BASICS
        ##################################
        print_line_with_subheader2("MD run parameters")
        print("Simulation time: {} ps".format(simulation_time))
        print("Simulation steps: {}".format(simulation_steps))
        print("Timestep: {} ps".format(self.timestep))
        print("Set temperature: {} K".format(self.temperature))
        print("OpenMM integrator:", self.openmmobject.integrator_name)
        print("self.openmmobject.integrator:", self.openmmobject.integrator)
        print()
        forceclassnames = [i.__class__.__name__ for i in self.openmmobject.system.getForces()]
        print("OpenMM System forces present before run:", forceclassnames)

        #Printing PBCs
        if self.openmmobject.Periodic is True:
            print("Checking Initial PBC vectors.")
            self.state = simulation.context.getState()
            a, b, c = self.state.getPeriodicBoxVectors()
            print(f"A: ", a)
            print(f"B: ", b)
            print(f"C: ", c)
            boxlength = a[0].value_in_unit(openmm.unit.angstrom) #Box length in Angstrom
            print(f"Boxlength: {boxlength} Angstrom")
        else:
            print("System is not periodic")
        # Delete old traj
        #try:
        #    os.remove("OpenMMMD_traj.xyz")
        ## Crashes when permissions not present or file is folder. Should never occur.
        #except FileNotFoundError:
        #    pass

        #Make sure file associated with StateDataReporter is open
        if self.datafilename is not None:
            #RB addition: Delete file after each run
            print("Deleting old datafile:", self.datafilename)
            try:
                os.remove(self.datafilename)
            except:
                pass
            self.dataoutputoption = open(self.datafilename,'a')

        #Setup data and simulation reporters for simulation object
        self.set_sim_reporters(simulation)

        # Setting coordinates of OpenMM object from current fragment.coords
        self.openmmobject.set_positions(self.positions,simulation)
        print()

        if self.QM_MM_object is not None:
            print("QM/MM MD run beginning")
            #CASE: QM/MM. Custom external force needs to have been created in OpenMMTheory (should be handled by init)
            
            #Get connectivity from OpenMM topology
            connectivity = []
            for resi in self.openmmobject.topology.residues():
                resatoms = [i.index for i in list(resi.atoms())]
                connectivity.append(resatoms)
            #Convert to dict
            connectivity_dict = create_conn_dict(connectivity)

            for step in range(simulation_steps):
                checkpoint_begin_step = time.time()
                checkpoint = time.time()
                if self.printlevel >= 2:
                    print("Step:", step)
                
                #Get state of simulation. Gives access to coords, velocities, forces, energy etc.
                current_state=simulation.context.getState(getPositions=True, enforcePeriodicBox=self.enforcePeriodicBox, getEnergy=True)
                print_time_rel(checkpoint, modulename="get OpenMM state", moduleindex=2, currprintlevel=self.printlevel, currthreshold=2)
                checkpoint = time.time()
                # Get current coordinates from state to use for QM/MM step
                current_coords = np.array(current_state.getPositions(asNumpy=True))*10

                #QM/MM periodic. Translating coords outside of box, back in
                if self.openmmobject.Periodic is True:
                    print("Periodic QM/MM is on")
                    if self.enforcePeriodicBox is True:
                        print("enforcePeriodicBox is True. Wrapping handling by OpenMM")
                    elif self.enforcePeriodicBox is False:
                        print("enforcePeriodicBox is False. Wrapping handled by ASH")
                        print("Note: only cubic PBC boxes supported")
                        checkpoint = time.time()
                        current_coords = wrap_box_coords(current_coords,boxlength,connectivity_dict,connectivity)
                        print_time_rel(checkpoint, modulename="wrapping")

                #TODO: Translate box coordinates so that they are centered on solute
                #Do manually or use mdtraj, mdanalysis or something??
                #if self.specialbox is True:
                #    print("not ready")
                #    ashexit()
                #    solute_coords = np.take(current_coords, solute_indices, axis=0)
                #    changed_origin_coords = change_origin_to_centroid(self.fragment.coords, subsetcoords=solute_coords)
                #    current_coords = center_coordinates(current_coords,)

                
                #Printing step-info or write-trajectory at regular intervals
                if step % self.traj_frequency == 0:
                    # Manual step info option
                    if self.printlevel >= 2:
                        print_current_step_info(step,current_state,self.openmmobject)

                    #print("QM/MM step. Writing unwrapped to trajfile: OpenMMMD_traj_unwrapped.xyz")
                    #write_xyzfile(self.fragment.elems, current_coords, "OpenMMMD_traj_unwrapped", printlevel=1, writemode='a')
                    
                    print("Writing wrapped coords to trajfile: OpenMMMD_traj_wrapped.xyz (for debugging)")
                    write_xyzfile(self.fragment.elems, current_coords, "OpenMMMD_traj_wrapped", printlevel=1, writemode='a')
                

                checkpoint = time.time()
                print_time_rel(checkpoint, modulename="get current_coords", moduleindex=2, currprintlevel=self.printlevel, currthreshold=2)
                # Run QM/MM step to get full system QM+PC gradient.
                self.QM_MM_object.run(current_coords=current_coords, elems=self.fragment.elems, Grad=True,
                                      exit_after_customexternalforce_update=True, charge=self.charge, mult=self.mult)
                print_time_rel(checkpoint, modulename="QM/MM run", moduleindex=2, currprintlevel=self.printlevel, currthreshold=2)
                
                # Now need to update OpenMM external force with new QM-PC force
                 #The QM_PC gradient (link-atom projected, from QM_MM object) is provided to OpenMM external force
                CheckpointTime = time.time()
                self.openmmobject.update_custom_external_force(self.openmm_externalforceobject,
                                                               self.QM_MM_object.QM_PC_gradient,simulation)
                print_time_rel(CheckpointTime, modulename='QM/MM openMM: update custom external force', moduleindex=2, 
                                currprintlevel=self.printlevel, currthreshold=1)
                
                # NOTE: Think about energy correction (currently skipped above)
                #Accessible: self.QM_MM_object.extforce_energy
                # Now take OpenMM step (E+G + displacement etc.)
                checkpoint = time.time()


                #OpenMM metadynamics
                if metadynamics == True:
                    if self.printlevel >= 2:
                        print("Now calling OpenMM native metadynamics and taking 1 step")
                    meta_object.step(simulation, 1)
                    print_time_rel(checkpoint, modulename="mtd sim step", moduleindex=2, currprintlevel=self.printlevel, currthreshold=2)
                    checkpoint = time.time()

                    #getCollectiveVariables
                    if step % metadyn_settings["saveFrequency"]*metadyn_settings["frequency"] == 0:
                        if self.printlevel >= 2:
                            print("MTD: Writing current collective variables to disk")
                        current_cv = meta_object.getCollectiveVariables(simulation)
                        if metadyn_settings["CV1_type"] == "distance" or metadyn_settings["CV1_type"] == "bond" or metadyn_settings["CV1_type"] == "rmsd":
                            cv1scaling=10
                        elif metadyn_settings["CV1_type"] == "dihedral" or metadyn_settings["CV1_type"] == "torsion" or metadyn_settings["CV1_type"] == "angle":
                            cv1scaling=180/np.pi
                        if metadyn_settings["CV2_type"] == "distance" or metadyn_settings["CV2_type"] == "bond" or metadyn_settings["CV2_type"] == "rmsd":
                            cv2scaling=10
                        elif metadyn_settings["CV2_type"] == "dihedral" or metadyn_settings["CV2_type"] == "torsion" or metadyn_settings["CV2_type"] == "angle":
                            cv2scaling=180/np.pi
                        currtime = step*self.timestep #Time in ps
                        with open(f'colvar', 'a') as f:
                            if metadyn_settings["numCVs"] == 2:
                                f.write(f"{currtime} {current_cv[0]*cv1scaling} {current_cv[1]*cv2scaling}\n")
                            elif metadyn_settings["numCVs"] == 1:
                                f.write(f"{currtime} {current_cv[0]*cv1scaling}\n")
                    print_time_rel(checkpoint, modulename="mtd colvar-flush", moduleindex=2, currprintlevel=self.printlevel, currthreshold=2)
                    checkpoint = time.time()
                else:
                    simulation.step(1)
                    print_time_rel(checkpoint, modulename="openmmobject sim step", moduleindex=2, currprintlevel=self.printlevel, currthreshold=2)
                    checkpoint = time.time()
                print_time_rel(checkpoint_begin_step, modulename="Total sim step", moduleindex=2, currprintlevel=self.printlevel, currthreshold=2)
                
                # NOTE: Better to use OpenMM-plumed interface
                # After MM step, grab coordinates and forces
                #if self.plumed_object is not None:
                #    print("Plumed active. Untested. Hopefully works.")
                #    ashexit()
                #    #Necessary to call again
                #    current_state_forces=simulation.context.getState(getForces=True, enforcePeriodicBox=self.enforcePeriodicBox,)
                #    current_coords = np.array(current_state.getPositions(asNumpy=True)) #in nm
                #    current_forces = np.array(current_state_forces.getForces(asNumpy=True)) # in kJ/mol /nm
                #    # Plumed object needs to be configured for OpenMM
                #    energy, newforces = self.plumed_object.run(coords=current_coords, forces=current_forces,
                #                                               step=step)
                #    self.openmmobject.update_custom_external_force(self.plumedcustomforce, newforces,simulation)

        #External QM for OpenMMtheory
        #Used to run QM dynamics with OpenMM
        elif self.externalqm is True:
            if self.printlevel >= 2:
                print("External QM with OpenMM option")
            for step in range(simulation_steps):
                checkpoint_begin_step = time.time()
                checkpoint = time.time()
                if self.printlevel >= 2:
                    print("Step:", step)
                #Get state of simulation. Gives access to coords, velocities, forces, energy etc.
                current_state=simulation.context.getState(getPositions=True, enforcePeriodicBox=self.enforcePeriodicBox, getEnergy=True)
                print_time_rel(checkpoint, modulename="get OpenMM state", moduleindex=2, currprintlevel=self.printlevel, currthreshold=2)
                checkpoint = time.time()
                # Get current coordinates from state to use for QM/MM step
                current_coords = np.array(current_state.getPositions(asNumpy=True))*10
                print_time_rel(checkpoint, modulename="get current coords", moduleindex=2, currprintlevel=self.printlevel, currthreshold=2)
                checkpoint = time.time()

                #Printing step-info or write-trajectory at regular intervals
                if step % self.traj_frequency == 0:
                    # Manual step info option
                    if self.printlevel >= 2:
                        print_current_step_info(step,current_state,self.openmmobject)
                    #print_time_rel(checkpoint, modulename="print_current_step_info", moduleindex=2)
                    #checkpoint = time.time()
                    # Manual trajectory option (reporters do not work for manual dynamics steps)
                    #write_xyzfile(self.fragment.elems, current_coords, "OpenMMMD_traj", printlevel=1, writemode='a')
                    #print_time_rel(checkpoint, modulename="OpenMM_MD writetraj", moduleindex=2)
                    #checkpoint = time.time()

                # Run QM step to get full system QM gradient.
                # Updates OpenMM object with QM forces
                energy,gradient=self.qmtheory.run(current_coords=current_coords, elems=self.fragment.elems, Grad=True, charge=self.charge, mult=self.mult)
                if self.printlevel >= 2:
                    print("Energy:", energy)
                print_time_rel(checkpoint, modulename="QM run", moduleindex=2, currprintlevel=self.printlevel, currthreshold=2)
                self.openmmobject.update_custom_external_force(self.qmcustomforce,gradient,simulation)

                #Calculate energy associated with external force so that we can subtract it later
                #TODO: take this and QM energy and add to print_current_step_info
                extforce_energy=3*np.mean(sum(gradient*current_coords*1.88972612546))
                if self.printlevel >= 2:
                    print("extforce_energy:", extforce_energy)

                #OpenMM metadynamics
                if metadynamics == True:
                    if self.printlevel >= 2:
                        print("Now calling OpenMM native metadynamics and taking 1 step")
                    meta_object.step(simulation, 1)

                    #getCollectiveVariables
                    if step % metadyn_settings["saveFrequency"]*metadyn_settings["frequency"] == 0:
                        if self.printlevel >= 2:
                            print("MTD: Writing current collective variables to disk")
                        current_cv = meta_object.getCollectiveVariables(simulation)
                        if metadyn_settings["CV1_type"] == "distance" or metadyn_settings["CV1_type"] == "bond" or metadyn_settings["CV1_type"] == "rmsd":
                            cv1scaling=10
                        elif metadyn_settings["CV1_type"] == "dihedral" or metadyn_settings["CV1_type"] == "torsion" or metadyn_settings["CV1_type"] == "angle":
                            cv1scaling=180/np.pi
                        if metadyn_settings["CV2_type"] == "distance" or metadyn_settings["CV2_type"] == "bond" or metadyn_settings["CV2_type"] == "rmsd":
                            cv2scaling=10
                        elif metadyn_settings["CV2_type"] == "dihedral" or metadyn_settings["CV2_type"] == "torsion" or metadyn_settings["CV2_type"] == "angle":
                            cv2scaling=180/np.pi
                        currtime = step*self.timestep #Time in ps
                        with open(f'colvar', 'a') as f:
                            if metadyn_settings["numCVs"] == 2:
                                f.write(f"{currtime} {current_cv[0]*cv1scaling} {current_cv[1]*cv2scaling}\n")
                            elif metadyn_settings["numCVs"] == 1:
                                f.write(f"{currtime} {current_cv[0]*cv1scaling}\n")
                else:
                    simulation.step(1)
                print_time_rel(checkpoint, modulename="OpenMM sim step", moduleindex=2, currprintlevel=self.printlevel, currthreshold=2)
                print_time_rel(checkpoint_begin_step, modulename="Total sim step", moduleindex=2, currprintlevel=self.printlevel, currthreshold=2)


                # NOTE: Better to use OpenMM-plumed interface
                # After MM step, grab coordinates and forces
                #if self.plumed_object is not None:
                #    print("Plumed active. Untested. Hopefully works.")
                #    ashexit()
                #    #Necessary to call again
                #    current_state_forces=simulation.context.getState(getForces=True, enforcePeriodicBox=self.enforcePeriodicBox,)
                #    #Keep coords as default OpenMM nm and forces ad kJ/mol/nm. Avoid conversion
                #    plumed_coords = np.array(current_state.getPositions(asNumpy=True)) #in nm
                #    plumed_forces = np.array(current_state_forces.getForces(asNumpy=True)) # in kJ/mol /nm
                #    # Plumed object needs to be configured for OpenMM
                #    energy, newforces = self.plumed_object.run(coords=plumed_coords, forces=plumed_forces,
                #                                               step=step)
                #    self.openmmobject.update_custom_external_force(self.plumedcustomforce, newforces, 
                #                                                   simulation,conversion_factor=1.0)

        #TODO: Delete at some point once testing and debugging are over
        elif self.dummy_MM is True:
            print("Dummy MM option")
            for step in range(simulation_steps):
                checkpoint_begin_step = time.time()
                checkpoint = time.time()
                print("Step:", step)
                #Get state of simulation. Gives access to coords, velocities, forces, energy etc.
                current_state=simulation.context.getState(getPositions=True, enforcePeriodicBox=self.enforcePeriodicBox, getEnergy=True)
                print_time_rel(checkpoint, modulename="get OpenMM state", moduleindex=2, currprintlevel=self.printlevel, currthreshold=2)
                checkpoint = time.time()
                # Get current coordinates from state to use for QM/MM step
                current_coords = np.array(current_state.getPositions(asNumpy=True))*10
                print_time_rel(checkpoint, modulename="get current coords", moduleindex=2, currprintlevel=self.printlevel, currthreshold=2)
                checkpoint = time.time()
                #Printing step-info or write-trajectory at regular intervals
                if step % self.traj_frequency == 0:
                    # Manual step info option
                    print_current_step_info(step,current_state,self.openmmobject)
                    print_time_rel(checkpoint, modulename="print_current_step_info", moduleindex=2, currprintlevel=self.printlevel, currthreshold=2)
                    checkpoint = time.time()
                    # Manual trajectory option (reporters do not work for manual dynamics steps)
                    #write_xyzfile(self.fragment.elems, current_coords, "OpenMMMD_traj", printlevel=1, writemode='a')
                    #print_time_rel(checkpoint, modulename="OpenMM_MD writetraj", moduleindex=2)
                    #checkpoint = time.time()

                simulation.step(1)
                print_time_rel(checkpoint, modulename="OpenMM sim step", moduleindex=2, currprintlevel=self.printlevel, currthreshold=2)
                print_time_rel(checkpoint_begin_step, modulename="Total sim step", moduleindex=2, currprintlevel=self.printlevel, currthreshold=2)
        else:
            #OpenMM metadynamics
            if metadynamics == True:
                print("Now calling OpenMM native metadynamics")
                meta_object.step(simulation, simulation_steps)
            else:
                print("Regular classical OpenMM MD option chosen.")
                #This is the fastest option as getState is never called in each loop iteration like above
                # Running all steps in one go
                simulation.step(simulation_steps)

        print_line_with_subheader2("OpenMM MD simulation finished!")

        
        #Delete dummyatoms if defined
        #NOTE: probably not possible
        #if self.dummyatomrestraint is True:
        #    print("Removing dummy atom from OpenMM topology and system")
        #    self.openmmobject.remove_dummy_atom()

        #Close Statadatareporter file if open
        if self.datafilename != None:
            self.dataoutputoption.close()


        # Close Plumed also if active. Flushes HILLS/COLVAR etc.
        if self.plumed_object is not None:
            self.plumed_object.close()

        # enforcePeriodicBox=True
        self.state = simulation.context.getState(getEnergy=True, getPositions=True, getForces=True, enforcePeriodicBox=self.enforcePeriodicBox)
        print("Checking PBC vectors:")
        a, b, c = self.state.getPeriodicBoxVectors()
        print(f"A: ", a)
        print(f"B: ", b)
        print(f"C: ", c)

        # Set new PBC vectors since they may have changed
        print("Updating PBC vectors.")
        # Context. Used?
        simulation.context.setPeriodicBoxVectors(a, b, c)
        # System. Necessary
        self.openmmobject.system.setDefaultPeriodicBoxVectors(a, b, c)

        # Writing final frame to disk as PDB. 
        # NOTE: Convenient for using as a topology file for mdtraj
        with open(self.trajfilename+'.pdb', 'w') as f:
            openmm.app.pdbfile.PDBFile.writeHeader(self.openmmobject.topology, f)
        with open(self.trajfilename+'.pdb', 'a') as f:
            openmm.app.pdbfile.PDBFile.writeModel(self.openmmobject.topology,
                                                                    self.state.getPositions(asNumpy=True).value_in_unit(
                                                                        openmm.unit.angstrom), f)
        # Updating ASH fragment
        newcoords = self.state.getPositions(asNumpy=True).value_in_unit(openmm.unit.angstrom)
        print("Updating coordinates in ASH fragment.")
        self.fragment.coords = newcoords
        #Updating positions array also in case we call run again
        self.positions = newcoords
        print_time_rel(module_init_time, modulename="OpenMM_MD run", moduleindex=1)
        return

#############################
#  Multi-step MD protocols  #
#############################

#Note: dummyatomrestraints necessary for NPT simulation when constraining atoms in space
def OpenMM_box_relaxation(fragment=None, theory=None, datafilename="nptsim.csv", numsteps_per_NPT=10000,
                          volume_threshold=1.3, density_threshold=0.0012, temperature=300, timestep=0.004,
                          traj_frequency=100, trajfilename='relaxbox_NPT', trajectory_file_option='DCD', 
                          coupling_frequency=1, enforcePeriodicBox=True, 
                          dummyatomrestraint=False, solute_indices=None, barostat_frequency=25):
    """NPT simulations until volume and density stops changing

    Args:
        fragment ([type], optional): [description]. Defaults to None.
        theory ([type], optional): [description]. Defaults to None.
        datafilename (str, optional): [description]. Defaults to "nptsim.csv".
        numsteps_per_NPT (int, optional): [description]. Defaults to 10000.
        volume_threshold (float, optional): [description]. Defaults to 1.0.
        density_threshold (float, optional): [description]. Defaults to 0.001.
        temperature (int, optional): [description]. Defaults to 300.
        timestep (float, optional): [description]. Defaults to 0.004.
        traj_frequency (int, optional): [description]. Defaults to 100.
        trajectory_file_option (str, optional): [description]. Defaults to 'DCD'.
        coupling_frequency (int, optional): [description]. Defaults to 1.
        barostat_frequency (int, optional): [description]. Defaults to 25 (timesteps).
    """


    print_line_with_mainheader("Periodic Box Size Relaxation")

    if fragment is None or theory is None:
        print("Fragment and theory required.")
        ashexit()

    if numsteps_per_NPT < traj_frequency:
        print("Parameter 'numpsteps_per_NPT' must be greater than 'traj_frequency', otherwise"
              " no data will be written during the relaxation!")
        ashexit()

    print_line_with_subheader2("Relaxation Parameters")
    print("Steps per NPT cycle:", numsteps_per_NPT)
    print(f"Step size: {timestep * 1000} fs")
    print("Density threshold:", density_threshold)
    print("Volume threshold:", volume_threshold)
    print("Intermediate MD trajectory data file:", datafilename)

    if len(theory.user_frozen_atoms) > 0:
        print("Frozen_atoms:", theory.user_frozen_atoms)
        print(BC.WARNING,"OpenMM object has frozen atoms defined. This is known to cause strange issues for NPT simulations.",BC.END)
        print(BC.WARNING,"Check the results carefully!",BC.END)


    # Starting parameters
    steps = 0
    volume_std = 10
    density_std = 1

    md = OpenMM_MDclass(fragment=fragment, theory=theory, timestep=timestep, traj_frequency=traj_frequency,
                        temperature=temperature, integrator="LangevinMiddleIntegrator", enforcePeriodicBox=enforcePeriodicBox,
                        coupling_frequency=coupling_frequency, barostat='MonteCarloBarostat', trajfilename=trajfilename,
                        datafilename=datafilename, trajectory_file_option=trajectory_file_option,
                        dummyatomrestraint=dummyatomrestraint, solute_indices=solute_indices,
                        barostat_frequency=barostat_frequency)

    while volume_std >= volume_threshold and density_std >= density_threshold:
        md.run(numsteps_per_NPT)
        steps += numsteps_per_NPT

        # Read reporter file and calculate stdev
        NPTresults = read_NPT_statefile(datafilename)

        volume = NPTresults["volume"][-traj_frequency:]
        density = NPTresults["density"][-traj_frequency:]
        # volume = volume[-traj_frequency:]
        # density = density[-traj_frequency:]
        volume_std = np.std(volume)
        density_std = np.std(density)

        print_line_with_subheader2("Relaxation Status")
        print("Total steps taken:", steps)
        print(f"Total simulation time: {timestep * steps} ps")
        print("Current Volume:", volume[-1])
        print("Current Volume SD:", volume_std)
        print("Current Density", density[-1])
        print("Current Density SD", density_std)
        print("Volume SD threshold:", volume_threshold)
        print("Density SD threshold:", density_threshold)

    print("Relaxation of periodic box size finished!\n")
    return md.state.getPeriodicBoxVectors()


#Kinetic energy from velocities
def calc_kinetic_energy(velocities,dof):
    kin=0.0
    for v in velocities:
        kin+=0.5*np.dot(v,v)
    return 2*kin / (dof*ash.constants.BOLTZ)

#Used in OpenMM_MD when doing simulation step-by-step (e.g. QM/MM MD)
def print_current_step_info(step,state,openmmobject):
    import openmm
    kinetic_energy=state.getKineticEnergy()
    pot_energy=state.getPotentialEnergy()
    temp=(2*kinetic_energy/(openmmobject.dof*openmm.unit.MOLAR_GAS_CONSTANT_R)).value_in_unit(openmm.unit.kelvin)
    
    print("="*50)
    print("SIMULATION STATUS (STEP {})".format(step))
    print("_"*50)
    print("Time: {}".format(state.getTime()))
    print("Potential energy:", pot_energy)
    print("Kinetic energy:", kinetic_energy )
    print("Temperature: {}".format(temp))
    print("="*50)
    

#CHECKING PDB-FILE FOR multiple occupations.
#Default behaviour: 
# - if no multiple occupancies return input PDBfile and go on
# - if multiple occupancies, print list of residues and tell user to fix them. Exiting
# - if use_higher_occupancy is set to True, user higher occupancy location, write new PDB_file and use

def find_alternate_locations_residues(pdbfile, use_higher_occupancy=False):
    if use_higher_occupancy is True:
        print("Will keep higher occupancy atoms for alternate locations")
    
    #List of ATOM/HETATM lines to grab from PDB-file
    pdb_atomlines=[]
    #Dict of residues with alternate location labels
    bad_resids_dict={}

    #Alternate location dict for atoms found
    altloc_dict={}

    #Looping through PDB-file
    with open(pdbfile) as pfile:
        for line in pfile:
            if line.startswith("ATOM") or line.startswith("HETATM"):
                altloc=line[16]
                #Adding info to dicts and adding marker if alternate location info present for atom
                if altloc != " ":
                    chain=line[21:22]
                    #New dict item with chain as key
                    if chain not in bad_resids_dict:
                        bad_resids_dict[chain] = []
                    resid=int(line[22:26].replace(" ", ""))
                    resname=line[17:20].replace(" ", "")
                    residue=resname+str(resid)
                    atomname=line[12:16].replace(" ","")
                    occupancy=float(line[54:60])
                    #Atomstring contains only the atom-information (not alt-location label)
                    atomstring=chain+"_"+resname+"_"+str(resid)+"_"+atomname
                    #Adding residue to dict
                    if residue not in bad_resids_dict[chain]:
                        bad_resids_dict[chain].append(residue)
                    #Adding atom-info to dict
                    altloc_dict[(atomstring,altloc)]=[altloc,occupancy,line]
                    #Adding atomstring to list as a marker
                    if ["REPLACE_",atomstring] not in pdb_atomlines:
                        pdb_atomlines.append(["REPLACE_",atomstring])
                #Use unmodifed ATOM line
                else:
                    pdb_atomlines.append(line)
            else:
                #Still keeping unmodified line
                pdb_atomlines.append(line)
    #For debugging
    #for k,v in altloc_dict.items():
    #    print(k, v)
    def find_index_of_sublist_with_max_col(l,index):
        max=0
        result=None
        for i,s in enumerate(l):
            if s[index] > max:
                max=s[index]
                result=i
        return result
    
    #Now going through pdb_atomlines, finding marker and looking up the best occupancy atom from altloc_dict
    finalpdblines=[]
    for pdbline in pdb_atomlines:

        if pdbline[0]== "REPLACE_":
            print("Alternate locations for atom:", pdbline[1])
            options=[]
            #Looping through altloc_dict items
            for i,j in altloc_dict.items():
                #Matching atomstring
                if i[0] == pdbline[1]:
                    options.append([j[0],j[1],j[2]])
            for l in options:
                pdblinestring=''.join(map(str,l[2:]))
                print(pdblinestring)
            #Get max occupancy item
            ind = find_index_of_sublist_with_max_col(options,1)
            fline = options[ind][2][:16] + " " + options[ind][2][16 + 1:]
            #print(f"Choosing line {fline} based on occupancy {options[ind][1]}.")
            print(f"Choosing line with occupancy {options[ind][1]}.")
            print("-"*90)
            if fline not in finalpdblines:
                finalpdblines.append(fline)
        else:
            finalpdblines.append(pdbline)

    if len(bad_resids_dict) > 0:
        print(BC.WARNING,"\nFound residues in PDB-file that have alternate location labels i.e. multiple occupancies:", BC.END)
        for chain,residues in bad_resids_dict.items():
            print(f"\nChain {chain}:")
            for res in residues:
                print(res)
        print(BC.WARNING,"\nThese residues should be manually inspected and fixed in the PDB-file before continuing", BC.END)
        #if alternatelocation_label != None:
        #    print(BC.WARNING,"\nalternatelocation_label option chosen. Will choose form {} and go on.\n".format(alternatelocation_label), BC.END)
        #    writelisttofile(pdb_atomlines, "system_afteratlocfixes.pdb", separator="")
        #    return "system_afteratlocfixes.pdb"
        if use_higher_occupancy is True:
            print(BC.WARNING,"\n Use higher-occupancy location opton was selected, so continuing.", BC.END)
            writelisttofile(finalpdblines, "system_afteratlocfixes.pdb", separator="")
            return "system_afteratlocfixes.pdb"
        else:
            print(BC.WARNING,"You should delete either the labelled A or B location of the residue-atom/atoms and then remove the A/B label from column 17 in the file")
            print("Alternatively, you can choose use_higher_occupancy=True keyword in OpenMM_Modeller and ASH will keep the higher occupied form and go on ", BC.END)
            print("Make sure that there is always an A or B form present.")
            print(BC.FAIL,"Exiting.", BC.END)
            ashexit()
    #Returning original pdbfile if all OK        

    return pdbfile

#Function to get nonbonded model parameters for a metal cluster
#TODO: Add option to symmetrize charges for similar atoms in residue
def write_nonbonded_FF_for_ligand(fragment=None, xyzfile=None, charge=None, mult=None, coulomb14scale=1.0, lj14scale=1.0, 
    charmm=True, charge_model="xTB", theory=None, LJ_model="UFF", resname="LIG"):
    print_line_with_mainheader("OpenMM write_nonbonded_FF_for_ligand")

    if charmm == True:
        print("CHARMM option: True")
        print("Will create XML file so that the Nonbonded Interaction is compatible with CHARMM.\n")

    else:
        print("CHARMM option: False")
        print("Will create XML file in the regular way\n")

    #Coulomb and LJ scaling. Needs to be FF compatible. CHARMM values below

    #Creating ASH fragment
    if fragment != None:
        if fragment.charge == None or fragment.mult == None:
            print("No charge/mult information present in fragment")
            if charge == None or mult == None:
                print("No charge/mult info provided to function write_nonbonded_FF_for_ligand either.")
                print("Exiting")
                ashexit()
            else:
                fragment.charge=charge; fragment.mult=mult

        #Charge
    elif xyzfile != None:
        if os.path.exists(xyzfile) == False:
            print("XYZ-file does not exist. Exiting")
            ashexit()
        if charge == None or mult == None :
            print("XYZ-file option requires charge and mult definition. Exiting.")
            ashexit()
        fragment=Fragment(xyzfile=xyzfile, charge=charge,mult=mult)
    else:
        print("Neither fragment or xyzfile was provided to write_nonbonded_FF_for_ligand")
        ashexit()

    # Defining simple atomnames and atomtypes to be used for ligand
    atomnames = [el + "Y" + str(i) for i, el in enumerate(fragment.elems)]
    atomtypes = [el + "X" + str(i) for i, el in enumerate(fragment.elems)]

    if charge_model == "xTB":
        print("Using xTB charges")
        charges = basic_atomcharges_xTB(fragment=fragment, charge=fragment.charge, mult=fragment.mult, xtbmethod='GFN2')
    elif charge_model == "CM5_ORCA":
        print("CM5_ORCA option chosen")
        if theory == None: print("theory keyword required");ashexit()
        atompropdict = basic_atom_charges_ORCA(fragment=fragment, charge=fragment.charge, mult=fragment.mult,
                                               orcatheory=theory, chargemodel="CM5", numcores=theory.numcores)
        charges = atompropdict['charges']
    else:
        print("Unknown nonbonded_pars option")
        exit()

    if LJ_model == "UFF":
        # Basic UFF LJ parameters
        # Converting r0 parameters from Ang to nm and to sigma
        sigmas = [UFF_modH_dict[el][0] * 0.1 / (2 ** (1 / 6)) for el in fragment.elems]
        # Convering epsilon from kcal/mol to kJ/mol
        epsilons = [UFF_modH_dict[el][1] * 4.184 for el in fragment.elems]
    else:
        print("other LJ model not available")
        ashexit()

    # Creating XML-file for ligand
    xmlfile = write_xmlfile_nonbonded(resnames=[resname], atomnames_per_res=[atomnames], atomtypes_per_res=[atomtypes],
                                        elements_per_res=[fragment.elems], masses_per_res=[fragment.masses],
                                        charges_per_res=[charges],
                                        sigmas_per_res=[sigmas], epsilons_per_res=[epsilons], filename=resname+".xml",
                                        coulomb14scale=coulomb14scale, lj14scale=lj14scale, charmm=charmm)
    return xmlfile



################################
# Native OpenMM metadynamics
################################

# Metadynamics written as a wrapper function around OpenMM_MDclass
#TODO: Decide units for CV biaswidth range and Gaussian height
#NOTE: Restraints are in Angstrom and kcal/mol^2
def OpenMM_metadynamics(fragment=None, theory=None, timestep=0.004, simulation_steps=None, simulation_time=None,
              traj_frequency=1000, temperature=300, integrator='LangevinMiddleIntegrator',
              barostat=None, pressure=1, trajectory_file_option='DCD', trajfilename='trajectory',
              coupling_frequency=1, charge=None, mult=None, platform='CPU', hydrogenmass=1.5, constraints=None,
              anderson_thermostat=False, restraints=None, flatbottom_restraint_CV1=None, flatbottom_restraint_CV2=None,
              enforcePeriodicBox=True, dummyatomrestraint=False, center_on_atoms=None, solute_indices=None,
              datafilename=None, dummy_MM=False, plumed_object=None, add_center_force=False,
              center_force_atoms=None, centerforce_constant=1.0, barostat_frequency=25, specialbox=False,
              use_plumed=False, plumed_input_string=None,
              CV1_atoms=None, CV2_atoms=None, CV1_type=None, CV2_type=None, biasfactor=6, 
              height=1, 
              CV1_biaswidth=0.5, CV2_biaswidth=0.5, CV1_range=None, CV2_range=None,
              frequency=1, savefrequency=10, printlevel=2,
              biasdir='.', multiplewalkers=False, numcores=1, walkerid=None):
    print_line_with_mainheader("OpenMM metadynamics")
    
    #Biasdirectory
    print("biasdirectory chosen to be:", biasdir)
    biasdir_full_path = os.path.abspath(biasdir)
    print("Full path to biasdirectory is:", biasdir_full_path)

    if CV1_atoms == None or CV1_type == None:
        print("Error: You must specify both CV1_atoms and CV1_type keywords")
        ashexit()

    if CV2_atoms == None or CV2_type == None:
        print("CV2 not specified. Assuming only 1 CV in simulation.")
        numCVs=1
    else:
        numCVs=2

    #Parallelization
    if multiplewalkers is True and numcores == 1 :
        print("Error: For multiplewalkers=True  you must set numcores to the number of walkers")
        ashexit()
    

    if use_plumed is True:
        print("Using metadynamics via OpenMM Plumed plugin (use_plumed=True)")

        #TODO: Trying to load plumed, test for plugin and also plumed package
        try:
            #from openmmplumed import PlumedForce
            import openmmplumed
        except ModuleNotFoundError:
            print("openmmplumed module plugin not found. See https://github.com/openmm/openmm-plumed \nYou can install via conda: \nconda install -c conda-forge openmm-plumed")
            ashexit()
    else:
        print("Using OpenMM built-in metadynamics option (use_plumed=False)")


    #Creating MDclass
    md = OpenMM_MDclass(fragment=fragment, theory=theory, charge=charge, mult=mult, timestep=timestep,
                        traj_frequency=traj_frequency, temperature=temperature, integrator=integrator, constraints=constraints,
                        barostat=barostat, pressure=pressure, trajectory_file_option=trajectory_file_option,
                        coupling_frequency=coupling_frequency, anderson_thermostat=anderson_thermostat,
                        enforcePeriodicBox=enforcePeriodicBox, dummyatomrestraint=dummyatomrestraint, center_on_atoms=center_on_atoms, solute_indices=solute_indices,
                        datafilename=datafilename, dummy_MM=dummy_MM, platform=platform, hydrogenmass=hydrogenmass,
                        plumed_object=plumed_object, add_center_force=add_center_force,trajfilename=trajfilename,
                        center_force_atoms=center_force_atoms, centerforce_constant=centerforce_constant,
                        barostat_frequency=barostat_frequency, specialbox=specialbox, printlevel=printlevel)

    #Load OpenMM.app
    import openmm
    import openmm.app as openmm_app

    #If RMSD CV
    if CV1_type == 'rmsd' or CV2_type == 'rmsd':
        #Reference position. For now just use initial cooordinates as reference positions
        #coords_nm = fragment.coords * 0.1  # converting from Angstrom to nm
        #reference_pos = [openmm.Vec3(coords_nm[i, 0], coords_nm[i, 1], coords_nm[i, 2]) for i in
        #       range(len(coords_nm))] * openmm.unit.nanometer
        print("rmsd_CV1_reference_indices:", CV1_atoms)
        print("rmsd_CV2_reference_indices:", CV2_atoms)
    else:
        reference_pos=None
    #Setting up collective variables for native case or plumed case
    if use_plumed is False:
        native_MTD=True
        plumedinput=None
        #Creating dictionary with MTD parameters that will be passed to MD function
        if numCVs == 1:
            # Create metadynamics dict for 1 CV
            metadyn_settings = {"numCVs":numCVs, "temperature":temperature, "biasfactor":biasfactor, 
                                "height":height, "frequency":frequency, "saveFrequency":savefrequency, "biasdir":biasdir_full_path,
                                "CV1_type":CV1_type,"CV2_type":None,
                                "CV1_atoms":CV1_atoms,"CV2_atoms":CV2_atoms, "CV1_range":CV1_range, "CV2_range":CV2_range, 
                                "CV1_biaswidth":CV1_biaswidth,"CV2_biaswidth":CV2_biaswidth,
                                "CV2_minvalue":None,"CV2_maxvalue":None, 
                                "flatbottom_restraint_CV1":flatbottom_restraint_CV1, "flatbottom_restraint_CV2":flatbottom_restraint_CV2}
        elif numCVs == 2:
            # Create metadynamics object for 2 CVs
            metadyn_settings = {"numCVs":numCVs, "temperature":temperature, "biasfactor":biasfactor, 
                                "height":height, "frequency":frequency, "saveFrequency":savefrequency, "biasdir":biasdir_full_path,
                                "CV1_type":CV1_type,"CV2_type":CV2_type,
                                "CV1_range":CV1_range, "CV2_range":CV2_range, 
                                "CV1_atoms":CV1_atoms,"CV2_atoms":CV2_atoms, "CV1_biaswidth":CV1_biaswidth,"CV2_biaswidth":CV2_biaswidth,
                                "flatbottom_restraint_CV1":flatbottom_restraint_CV1, "flatbottom_restraint_CV2":flatbottom_restraint_CV2}
    else:
        print("Setting up Plumed")
        #Setting native_MTD Boolean to False and metaobject to None
        native_MTD=False
        metadyn_settings=None
        #OPTION to provide the full Plumed input as string instead
        if plumed_input_string != None:
            print("plumed_input_string provided. Will read all options from this string (make sure to provide atom indices in 1-based indexing)")
            writestringtofile(plumed_input_string,"plumedinput.in")
            plumedinput=plumed_input_string
        #CREATE Plumed input strings based on provided keyword options
        else:
            print("No plumed_input_string provided. Will create based on user-input")
            plumedinput = setup_plumed_input(savefrequency,numCVs,height,temperature,biasfactor,
                       CV1_type,CV1_biaswidth,CV1_atoms,
                       CV2_type,CV2_biaswidth,CV2_atoms,
                       multiplewalkers=multiplewalkers, biasdir=biasdir_full_path,
                       walkernum=numcores,
                       walkerid=walkerid)
            writestringtofile(plumedinput,"plumedinput.in")
        
        #NOTE: Ading PlumedForce to OpenMM system now done inside md.run instead

    #Updating simulation context as the CustomCVForce needs to be added
    #Unnecessary as md.run will create_simulation
    #md.openmmobject.create_simulation()

    #Calling md.run with either native option active or false
    print("Now starting metadynamics simulation")

    if multiplewalkers is True:
        print(f"Now launching Metadynamics job with {numcores} walkers")
        #Input parameters passed as dictionary to Simple_parallel
        #NOTE: multiprocess library (instead of multiprocessing) is necessary.
        #Otherwise pickling problem involving _io.TextIOWrapper
        ash.functions.functions_parallel.Simple_parallel(jobfunction=
                                                        md.run, parameter_dict={"simulation_steps":simulation_steps, 
                                                        "simulation_time":simulation_time, "metadynamics":native_MTD, 
                                                        "metadyn_settings":metadyn_settings, "plumedinput" : plumedinput}, 
                                                        numcores=numcores, version='multiprocess', separate_dirs=True,
                                                        restraints=restraints)
    else:
        simulation = md.run(simulation_steps=simulation_steps, simulation_time=simulation_time, metadynamics=native_MTD, metadyn_settings=metadyn_settings,
                            restraints=restraints)
    print("Metadynamics simulation done")

    #Data plotting
    if use_plumed is False:
        print("\nAll bias-files have been written to biasdirectory:", biasdir_full_path)
        print("Dir also contains: ASH_MTD_parameters.txt")
        print("Use function  get_free_energy_from_biasfiles  to create free-energy surface")
        print("and function metadynamics_plot_data to plot the data")
        print()
    else:
        path_to_plumed=os.path.dirname(os.path.dirname(os.path.dirname(openmmplumed.mm.pluginLoadedLibNames[0])))
        print("You can now call MTD_analyze in a separate ASH script to analyze/plot data (requires presence of HILLS and COLVAR files in directory)")
        print("Example:")
        if numCVs == 1:
            print(f"MTD_analyze(path_to_plumed={path_to_plumed}, CV1_type='{CV1_type}', temperature={temperature}, \
CV1_indices={CV1_atoms}, plumed_energy_unit='kj/mol', Plot_To_Screen=False)")        
        elif numCVs == 2:
            print(f"MTD_analyze(path_to_plumed={path_to_plumed}, CV1_type='{CV1_type}', CV2_type='{CV2_type}', temperature={temperature}, \
CV1_indices={CV1_atoms}, CV2_indices={CV2_atoms}, plumed_energy_unit='kj/mol', Plot_To_Screen=False)")
        print("\n")
    return

#
def Gentle_warm_up_MD(theory=None, fragment=None, time_steps=[0.0005,0.001,0.004], steps=[10,50,10000], 
    temperatures=[1,10,300], check_gradient_first=True, gradient_threshold=100, use_mdtraj=True, 
    trajfilename="warmup_MD", initial_opt=True, traj_frequency=1, maxoptsteps=10, coupling_frequency=1):
    print_line_with_mainheader("Gentle_warm_up_MD")
    print("Trajectory filename:", trajfilename)
    if theory is None or fragment is None:
        print("Gentle_warm_up_MD requires theory (OpenMM object) and fragment")
        ashexit()

    if len(time_steps) != len(steps) or len(time_steps) != len(temperatures):
        print("Error: Lists time_steps, steps and temperatures all need to be the same length. Exiting")
        ashexit()

    #Gradient check before we proceed
    if check_gradient_first is True:
        print("check_gradient_first is True")
        print("Will run singlepoint gradient calculation to check for large forces")
        theory.force_run=True
        SP_result = Singlepoint(theory=theory, fragment=fragment, Grad=True)
        badindices = check_gradient_for_bad_atoms(fragment=fragment,gradient=SP_result.gradient, threshold=gradient_threshold)
        if len(badindices) > 0:
            print(f"\nNumber of atoms with large forces: {len(badindices)}")
            print("Suggests a bad system geometry or that atoms need constraints (might be present already)")
            print("Gentle_warm_up_MD will go on")

    #Try a simple minimization first or simple MD

    #nonHindices=fragment.get_nonH_atomindices() #get nonH indices
    #testheory.freeze_atoms(frozen_atoms=nonHindices) #freezing non-H atoms
    #testheory.remove_all_constraints() #remove all constraints (incompatible with frozen atoms)
    #testheory.update_simulation() #Updating simulation object after freezing
    if initial_opt is True:
        print(f"\ninitial_opt is True (default). Will attempt initial {maxoptsteps}-step minimization first")
        print("If this step runs forever something is wrong. Select initial_opt=False to avoid in this case")
        try:
            OpenMM_Opt(fragment=fragment, theory=theory, maxiter=maxoptsteps, tolerance=1)
            print("Minimization successful")
        except Exception as e :
            print("Problem minimizing system")
            print("Error message:", e)       
            print("Will go on to do MD")

    print(f"\n{len(steps)} MD-runs have been defined")
    for num, (ts, step, temp) in enumerate(zip(time_steps, steps, temperatures)):
        print(f"MD-step {num} Number of simulation steps: {step} with timestep: {ts} and temperature: {temp} K")

    print();print()
    #Gentle heating up protocol
    for num, (ts, step, temp) in enumerate(zip(time_steps, steps, temperatures)):
        #Name of PDB and DCD filename: i.e. warmup_MD_cycle1.pdb and warmup_MD_cycle1.dcd 
        MDcyclename=trajfilename+f"_cycle{num}"
        print(f"\n\nNow running MD-run {num}. Number of steps: {step} with timestep:{ts} and temperature: {temp} K")
        print(f"Will write trajectory to file: {MDcyclename}.dcd")
        OpenMM_MD(fragment=fragment, theory=theory, timestep=ts, simulation_steps=step, traj_frequency=traj_frequency, temperature=temp,
            integrator='LangevinMiddleIntegrator', coupling_frequency=coupling_frequency, trajfilename=MDcyclename, trajectory_file_option='DCD')
        
        #Running mdtraj after each sim
        if use_mdtraj is True:
            print("Trying to load mdtraj for basic analysis of trajectory")
            try:
                print("Imaging trajectory")
                MDtraj_imagetraj(f"{MDcyclename}.dcd", f"{MDcyclename}.pdb")
                print("\nRunning RMS Fluctuation analysis on trajectory")
                MDtraj_RMSF(f"{MDcyclename}.dcd", f"{MDcyclename}.pdb", print_largest_values=True, 
                    threshold=0.005, largest_values=10)
            except ImportError:
                print("mdtraj library could not be imported. Skipping")

    print("Gentle_warm_up_MD finished successfully!")

    return

#Function to create CV biases in native OpenMM metadynamics
def create_CV_bias(CV_type,CV_atoms,biaswidth_cv,CV_range=None, reference_pos=None, reference_particles=None):
    import openmm
    print("Inside create_CV_bias")
    print("CV_type:", CV_type)
    print("CV_atoms:", CV_atoms)
    #TODO: Try changing dihedrals/angles to deg units
    #Most of the time though there is no reason to specify CV min and max for these CVs as you want the full range
    # However the biaswidth is also in 
    if CV_range == None:
        print("Warning: No minx/max value range for CVchosen by user")
        print("Will choose reasonable values based on CV type:")
        if CV_type == "dihedral" or CV_type == "torsion":
            CV_min_val=-np.pi
            CV_max_val=np.pi
            CV_unit=openmm.unit.radians
            CV_unit_label="rad"
            biaswidth_cv_unit=openmm.unit.radians
            biaswidth_cv_unit_label="rad"
        elif CV_type == "angle":
            CV_min_val=0
            CV_max_val=np.pi
            CV_unit=openmm.unit.radians
            CV_unit_label="rad"
            biaswidth_cv_unit=openmm.unit.radians
            biaswidth_cv_unit_label="rad"
        elif CV_type == "distance" or CV_type == "bond":
            CV_min_val=0.0
            CV_max_val=5.0
            CV_unit=openmm.unit.angstroms
            CV_unit_label="Å"
            biaswidth_cv_unit=openmm.unit.angstroms
            biaswidth_cv_unit_label="Å"
        elif CV_type == "rmsd":
            CV_min_val=0.0
            CV_max_val=5.0
            CV_unit=openmm.unit.angstroms
            CV_unit_label="Å"
            biaswidth_cv_unit=openmm.unit.angstroms
            biaswidth_cv_unit_label="Å"
    else:
        print("CV range given.")
        CV_min_val=CV_range[0]
        CV_max_val=CV_range[1]
        if CV_type == "dihedral" or CV_type == "torsion":
            CV_unit=openmm.unit.radians
            CV_unit_label="rad"
            biaswidth_cv_unit=openmm.unit.radians
            biaswidth_cv_unit_label="rad"
        elif CV_type == "angle":
            CV_unit=openmm.unit.radians
            CV_unit_label="rad"
            biaswidth_cv_unit=openmm.unit.radians
            biaswidth_cv_unit_label="rad"
        elif CV_type == "distance" or CV_type == "bond":
            CV_unit=openmm.unit.angstroms
            CV_unit_label="Å"
            biaswidth_cv_unit=openmm.unit.angstroms
            biaswidth_cv_unit_label="Å"
        elif CV_type == "rmsd":
            CV_unit=openmm.unit.angstroms
            CV_unit_label="Å"
            biaswidth_cv_unit=openmm.unit.angstroms
            biaswidth_cv_unit_label="Å"
    print(f"CV_min_val: {CV_min_val} and CV_max_val: {CV_max_val} {CV_unit_label}")
    print(f"Biaswidth of CV: {biaswidth_cv} {biaswidth_cv_unit_label}")
    # Define collective variables for CV1 and CV2.
    if CV_type == "dihedral" or CV_type == "torsion":
        if len(CV_atoms) != 4:
            print("Error: CV_atoms list must contain 4 atom indices")
            ashexit()
        cvforce = openmm.CustomTorsionForce('theta')
        cvforce.addTorsion(*CV_atoms)
        CV_bias = openmm.app.BiasVariable(cvforce, CV_min_val*CV_unit, CV_max_val*CV_unit, biaswidth_cv*biaswidth_cv_unit, periodic=True)
        #CV_bias = openmm.app.BiasVariable(cv, -np.pi, np.pi, biaswidth_cv, True)
    elif CV_type == "angle":
        if len(CV_atoms) != 3:
            print("Error: CV_atoms list must contain 3 atom indices")
            ashexit()
        cvforce = openmm.CustomAngleForce('theta')
        cvforce.addAngle(*CV_atoms)
        CV_bias = openmm.app.BiasVariable(cvforce, CV_min_val*CV_unit, CV_max_val*CV_unit, biaswidth_cv*biaswidth_cv_unit, periodic=False)
    elif CV_type == "distance" or CV_type == "bond":
        if len(CV_atoms) != 2:
            print("Error: CV_atoms list must contain 2 atom indices")
            ashexit()
        cvforce = openmm.CustomBondForce('r')
        cvforce.addBond(*CV_atoms)
        CV_bias = openmm.app.BiasVariable(cvforce, CV_min_val*CV_unit, CV_max_val*CV_unit, biaswidth_cv*biaswidth_cv_unit, periodic=False)
    elif CV_type == "rmsd":
        #http://docs.openmm.org/development/api-python/generated/openmm.openmm.RMSDForce.html
        #reference_pos: A vector of atom positions
        #reference_particles: atom indices used to calculate RMSD
        cvforce = openmm.RMSDForce(reference_pos)
        cvforce.setParticles(reference_particles)
        CV_bias = openmm.app.BiasVariable(cvforce, CV_min_val*CV_unit, CV_max_val*CV_unit, biaswidth_cv*biaswidth_cv_unit, periodic=False)
    else:
        print("unsupported CV_type for native OpenMM metadynamics implementation")
        ashexit()

    return CV_bias,cvforce

#Standalone function to create Plumed input-string based on basic MTD info and CVs
#NOTE: SIGMA. Possible unit conversion needed here?
#NOTE: grid min and max settings
#NOTE: distance_mingrid and distance_maxgrid controls min and max for distances.
#dihedrals and angles are -pi to pi and 0 to pi
def setup_plumed_input(savefrequency,numCVs,height,temperature,biasfactor,
                       CV1_type,biaswidth_cv1,CV1_atoms,
                       CV2_type,biaswidth_cv2,CV2_atoms,
                       distance_mingrid=0.05, distance_maxgrid=0.3,
                       multiplewalkers=False, biasdir='.', walkernum=None,
                       walkerid=None):
    print("Inside setup_plumed_input")
    strideval=savefrequency #allow different ?
    paceval=savefrequency # allow different ?

    #FIRST SETTING UP CV1:
    if CV1_type == "dihedral" or CV1_type == "torsion":
        grid_min1="-pi"; grid_max1="pi"; sigma_cv1=biaswidth_cv1
        cv1atom_line=f"CV1: TORSION ATOMS={CV1_atoms[0]+1},{CV1_atoms[1]+1},{CV1_atoms[2]+1},{CV1_atoms[3]+1}"
    elif CV1_type == "angle":
        grid_min1="0"; grid_max1="pi"; sigma_cv1=biaswidth_cv1
        cv1atom_line=f"CV1: ANGLE ATOMS={CV1_atoms[0]+1},{CV1_atoms[1]+1},{CV1_atoms[2]+1}"
    elif CV1_type == "bond" or CV1_type == "distance":
        grid_min1=distance_mingrid; grid_max1=distance_maxgrid; sigma_cv1=biaswidth_cv1
        cv1atom_line=f"CV1: DISTANCE ATOMS={CV1_atoms[0]+1},{CV1_atoms[1]+1}"
    else:
        print("Error:Unknown CV1_type option")
        ashexit()
    if numCVs == 1:
        print("numCVs: 1")
        if multiplewalkers is True:
            #NOTE: WALKERID set later
            walker_string=f"""WALKERS_N={walkernum} WALKERS_ID=WALKERID WALKERS_DIR={biasdir} WALKERS_RSTRIDE={strideval}"""
        else:
            walker_string=""
        plumedinput = f"""
{cv1atom_line}        
metad: METAD ARG=CV1 SIGMA={sigma_cv1} GRID_MIN={grid_min1} GRID_MAX={grid_max1} HEIGHT={height} PACE={paceval} TEMP={temperature} BIASFACTOR={biasfactor} FMT=%14.6f {walker_string}
PRINT STRIDE={strideval} ARG=CV1,metad.bias FILE=COLVAR
        """
        return plumedinput
    
    #2 CVs
    elif numCVs == 2:
        print("numCVs: 2")
    #SETTING UP CV2:
    if CV2_type == "dihedral" or CV2_type == "torsion":
        grid_min2="-pi"; grid_max2="pi"; sigma_cv2=biaswidth_cv1
        cv2atom_line=f"CV2: TORSION ATOMS={CV2_atoms[0]+1},{CV2_atoms[1]+1},{CV2_atoms[2]+1},{CV2_atoms[3]+1}"
    elif CV2_type == "angle":
        grid_min2="0"; grid_max2="pi"; sigma_cv2=biaswidth_cv1
        cv2atom_line=f"CV2: ANGLE ATOMS={CV2_atoms[0]+1},{CV2_atoms[1]+1},{CV2_atoms[2]+1}"
    elif CV2_type == "bond" or CV2_type == "distance":
        grid_min2=distance_mingrid; grid_max2=distance_maxgrid; sigma_cv2=biaswidth_cv2
        cv2atom_line=f"CV2: DISTANCE ATOMS={CV2_atoms[0]+1},{CV2_atoms[1]+1}"
    else:
        print("Error:Unknown CV1_type option")
        ashexit()
    #MULTIPLE WALKERS
    if multiplewalkers is True:
        walker_string=f"""WALKERS_N={walkernum} WALKERS_ID=WALKERID WALKERS_DIR={biasdir} WALKERS_RSTRIDE={strideval}
        """
        plumedinput = f"""{cv1atom_line}
        {cv2atom_line}
        metad: METAD ARG=CV1,CV2 SIGMA={sigma_cv1},{sigma_cv2} GRID_MIN={grid_min1},{grid_min2} GRID_MAX={grid_max1},{grid_max2} HEIGHT={height} PACE={paceval} TEMP={temperature} BIASFACTOR={biasfactor} FMT=%14.6f {walker_string}
        PRINT STRIDE={strideval} ARG=CV1,CV2,metad.bias FILE=COLVAR
        """
    else:
        walker_string=""
        plumedinput = f"""{cv1atom_line}
        {cv2atom_line}
        metad: METAD ARG=CV1,CV2 SIGMA={sigma_cv1},{sigma_cv2} GRID_MIN={grid_min1},{grid_min2} GRID_MAX={grid_max1},{grid_max2} HEIGHT={height} PACE={paceval} TEMP={temperature} BIASFACTOR={biasfactor} FMT=%14.6f {walker_string}
        PRINT STRIDE={strideval} ARG=CV1,CV2,metad.bias FILE=COLVAR
        """
    return plumedinput


#Calculate free-energy from total bias array
def free_energy_from_bias_array(temperature,biasFactor,totalBias):
    deltaT = temperature*(biasFactor-1)
    kjpermoleconversion=1
    free_energy = -((temperature+deltaT)/deltaT)*totalBias*kjpermoleconversion
    return free_energy

#Calculate free-energy from OpenMM biasfiles
def get_free_energy_from_biasfiles(temperature,biasfactor,CV1_gridwith,CV2_gridwith,directory='.'):
    import glob
    #Checking gridwiths
    if CV2_gridwith == None:
        full_bias=np.zeros((CV1_gridwith))
    else:
	    full_bias=np.zeros((CV2_gridwith,CV1_gridwith))
    
    #Looping over bias-files
    print("full_bias shape:", full_bias.shape)
    list_of_biases=[]
    for biasfile in glob.glob(f"{directory}/*.npy"):
        print("Loading biasfile:", biasfile)
        try:
            data = np.load(biasfile)
            print("data shape:", data.shape)
            full_bias += data
            list_of_biases.append(data)
        except FileNotFoundError:
            print("File not found error: Simulation probably still running. skipping file")

    #print("full_bias list:", full_bias)
    #print("len full_bias:", len(full_bias))
    #Get final free energy (sum of all)
    free_energy = free_energy_from_bias_array(temperature,biasfactor,full_bias)
    
    #Get free-energy per biasfile
    list_of_free_energies=[]
    for l in list_of_biases:
        fe = free_energy_from_bias_array(temperature,biasfactor,l)
        list_of_free_energies.append(fe)
    #Save: np.savetxt("MTD_free_energy.txt", free_energy)
    #Load: free_energy = np.loadtxt("MTD_free_energy.txt")

    #Return final free_energy array and also list of free-energy-arrays for each biasfile
    return free_energy,list_of_free_energies

#Simple plotting for native OpenMM metadynamics via ASH 
#NOTE: plot_xlim/plot_ylim in final CV units (Ang for distance/rmsd and ° for dihedrals/angles)
#CV1_minvalue/CV1_maxvalue should be set before simulation
def metadynamics_plot_data(biasdir=None, dpi=200, imageformat='png', plot_xlim=None, plot_ylim=None ):
    import json
    #Read mtd settings dict from file
    metadyn_settings = json.load(open(f"{biasdir}/ASH_MTD_parameters.txt"))    

    CV1_type=metadyn_settings["CV1_type"]; CV2_type=metadyn_settings["CV2_type"]; temperature=metadyn_settings["temperature"]; 
    biasfactor=metadyn_settings["biasfactor"]; CV1_gridwidth=metadyn_settings["CV1_gridwidth"]
    print("metadyn_settings:", metadyn_settings)
    CV2_gridwidth=metadyn_settings["CV2_gridwidth"]
    
    CV1_minvalue=metadyn_settings["CV1_minvalue"] 
    CV1_maxvalue=metadyn_settings["CV1_maxvalue"]
    CV2_minvalue=metadyn_settings["CV2_minvalue"]
    CV2_maxvalue=metadyn_settings["CV2_maxvalue"]
    print(f"Using CV1_minvalue:{CV1_minvalue} CV1_maxvalue:{CV1_maxvalue}")
    print(f"Using CV2_minvalue:{CV2_minvalue} CV2_maxvalue:{CV2_maxvalue}")

    e_conversionfactor=4.184 #kJ/mol to kcal/mol
    if CV2_type != None:
        numCVs=2
    else:
        numCVs=1
    if numCVs == 2:
        if CV1_type == 'dihedral' or CV1_type == 'torsion' or CV1_type == 'angle' :
            cv1_conversionfactor =180/np.pi
            CV1_unit_label="°"
        elif CV1_type == 'bond' or CV1_type == 'distance' or CV1_type == 'rmsd':
            cv1_conversionfactor = 10.0
            CV1_unit_label="Å"
        if CV2_type == 'dihedral' or CV2_type == 'angle' or CV1_type == 'torsion' :
            cv2_conversionfactor =180/np.pi
            CV2_unit_label="°"
        elif CV2_type == 'bond' or CV2_type == 'distance' or CV2_type == 'rmsd':
            cv2_conversionfactor = 10.0
            CV2_unit_label="Å"

        #Get free energy surface from biasfiles
        free_energy, list_of_fes_from_biasfiles = get_free_energy_from_biasfiles(temperature,biasfactor,CV1_gridwidth,
                                                                                    CV2_gridwidth,directory=biasdir)
        #Relative free energy in kcal/mol
        rel_free_energy = (free_energy-np.min(free_energy))/e_conversionfactor
        #Coordinates in correct unit
        xvalues = [cv1_conversionfactor*(CV1_minvalue+((CV1_maxvalue - CV1_minvalue) / (CV1_gridwidth-1))*i) for i in range(0,CV1_gridwidth)]
        yvalues = [cv2_conversionfactor*(CV2_minvalue+((CV2_maxvalue - CV2_minvalue) / (CV2_gridwidth-1))*i) for i in range(0,CV2_gridwidth)]
        np.savetxt("MTD_free_energy.txt", free_energy)
        np.savetxt("MTD_free_energy_rel.txt", rel_free_energy)
        np.savetxt("CV1_coord_values.txt", xvalues)
        np.savetxt("CV2_coord_values.txt", yvalues)

        
        #Plot
        print("Now plotting:")
        try:
            import matplotlib.pyplot
        except:
            print("Problem importing matplotlib")
            return
        #2D CV plotting uisng scatter with colormap 
        #Colormap to use in 2CV plots.
        # Perceptually uniform sequential: viridis, plasma, inferno, magma, cividis
        #Others: # RdYlBu_r
        #See https://matplotlib.org/3.1.0/tutorials/colors/colormaps.html
        colormap_option3='RdYlBu_r'
        X2, Y2 = np.meshgrid(xvalues, yvalues)
        option3fig,option3ax = matplotlib.pyplot.subplots()
        cm = matplotlib.pyplot.cm.get_cmap(colormap_option3)
        colorscatter=option3ax.scatter(X2, Y2, c=rel_free_energy, marker='o', linestyle='-', linewidth=1, cmap=cm)
        #Colorbar
        cbar = matplotlib.pyplot.colorbar(colorscatter)
        cbar.set_label('ΔG (kcal/mol)',fontweight='bold', fontsize='xx-small')
        #Limits
        if plot_xlim != None:
            option3ax.set_xlim(plot_xlim[0], plot_xlim[1])
        if plot_ylim != None:
            option3ax.set_ylim(plot_ylim[0], plot_ylim[1])
        option3ax.set_xlabel(f'CV1:{CV1_type}  ({CV1_unit_label})')
        option3ax.set_ylabel(f'CV2:{CV2_type}  ({CV2_unit_label})')
        option3fig.savefig('MTD_CV1_CV2_.png', format=imageformat, dpi=dpi)
        print("Created file: MTD_CV1_CV2_.png")
        return
    
    elif numCVs == 1:

        if CV1_type == 'dihedral' or CV1_type == 'torsion' or CV1_type == 'angle':
            cv1_conversionfactor =180/np.pi
            CV1_unit_label="°"
        elif CV1_type == 'bond' or CV1_type == 'distance' or CV1_type == 'rmsd':
            cv1_conversionfactor = 10.0
            CV1_unit_label="Ang"
        free_energy, bla = get_free_energy_from_biasfiles(temperature,biasfactor,CV1_gridwidth,None,directory=biasdir)
        
        #X-values
        full_range = CV1_maxvalue - CV1_minvalue
        increment = full_range / (CV1_gridwidth-1)
        xvalues = [cv1_conversionfactor*(CV1_minvalue+increment*i) for i in range(0,CV1_gridwidth)]
        np.savetxt("CV1_coord_values.txt", xvalues)
        #Relative energy in kcal/mol
        rel_free_energy = (free_energy-min(free_energy))/e_conversionfactor
        print("rel_free_energy:", rel_free_energy)
        #Save stuff
        np.savetxt("MTD_free_energy.txt", free_energy)
        np.savetxt("MTD_free_energy_rel.txt", rel_free_energy)

        #Plot object
        print("Now plotting:")
        CVlabel=f"{CV1_type} ({CV1_unit_label})"
        y_axislabel="Energy (kcal(/mol))"
        eplot = ash.modules.module_plotting.ASH_plot("Metadynamics", num_subplots=1, x_axislabel=CVlabel, y_axislabel=y_axislabel)
        eplot.addseries(0, x_list=xvalues, y_list=rel_free_energy, legend=None, color='blue', line=True, scatter=False)
        eplot.savefig('MTD_CV1', imageformat=imageformat, dpi=dpi)
        return







#Option 1: imshow
#plt.clf()
#colormap_option1='RdYlBu_r' 
#option1fig,option1ax = matplotlib.pyplot.subplots()
#print("option1fig:", option1fig)
#print("option1ax:", option1ax)
#print("cv1_conversionfactor*CV1_minvalue:", cv1_conversionfactor*CV1_minvalue)
#print("cv1_conversionfactor*CV1_maxvalue:", cv1_conversionfactor*CV1_maxvalue)
#print("cv2_conversionfactor*CV2_minvalue:", cv2_conversionfactor*CV2_minvalue)
#print("cv2_conversionfactor*CV2_maxvalue:", cv2_conversionfactor*CV2_maxvalue)
#option1ax.imshow(rel_free_energy, cmap=colormap_option1, extent=[cv1_conversionfactor*CV1_minvalue, cv1_conversionfactor*CV1_maxvalue, 
#                                                                  cv2_conversionfactor*CV2_maxvalue, cv2_conversionfactor*CV2_minvalue],
#                                                                )
#option1fig.colorbar(option1ax)
#option1ax.set_xlabel(f'CV1({CV1_unit_label})')
#option1ax.set_ylabel(f'CV2({CV2_unit_label})')
#option1fig.savefig('MTD_CV2_option1.png', format=imageformat, dpi=dpi)

#ashexit()
#Option 2: contour plot with gridlines
#surfacedictionary={}
#for i_x,x in enumerate(xvalues):
#    for i_y,y in enumerate(yvalues):
#        surfacedictionary[(x,y)] = rel_free_energy[i_x,i_y]
#colormap_option2='RdYlBu_r' #inferno_r another option
#print("Printing option2 plot")
#ash.modules.module_plotting.contourplot(surfacedictionary, label='_MTD_option2',x_axislabel=f'CV1({CV1_unit_label})', y_axislabel=f'CV2 ({CV2_unit_label})', finalunit='kcal/mol', interpolation='Cubic', 
#    interpolparameter=10, colormap=colormap_option2, dpi=200, imageformat='png', RelativeEnergy=False, numcontourlines=50,
#    contour_alpha=0.75, contourline_color='black', clinelabels=False, contour_values=None, title="")

#Function to wrap coordinates of whole molecules outside box
def wrap_box_coords(allcoords,boxlength,connectivity_dict,connectivity):
    #checkpoint = time.time()
    boxlength_half=boxlength/2
    #Get atom indices for atoms that have a x,y or z coordinate outside box
    mask = np.any(np.abs(allcoords) > boxlength_half, axis=1)
    indices = np.where(mask)[0]
    #20488
    #Get indices of all whole molecules
    all_mol_indices = [connectivity[connectivity_dict.get(i)] for i in indices]
    #print(all_mol_indices)
    #print(all_mol_indices[3399])
    #Removing duplicates
    trimmed_all_mol_indices = trim_list_of_lists(all_mol_indices)
    #print(trimmed_all_mol_indices)
    #Get all coordinates
    allmol_coords = np.take(allcoords, trimmed_all_mol_indices, axis=0)
    #print(allmol_coords)
    #Check if all members are outside
    allmol_outside_bools = [np.any(np.abs(m) > boxlength_half, axis=1) for m in allmol_coords]
    #print(allmol_outside_bools)
    #print(allmol_outside_bools)
    #allmol_outside_bools_single = [np.all(j) for j in allmol_outside_bools]
    allmol_outside_bools_single = [np.all(j) for j in allmol_outside_bools]
    #print(allmol_outside_bools_single)
    #print(allmol_outside_bools_single)
    #exit()

    #allmol_outside_cols = [np.any(np.abs(m) > boxlength_half, axis=1) for m in allmol_coords]
    #Looping over indices
    #print(f"6Time:{time.time()-checkpoint}")
    for members,member_coords,mol_outside_bool in zip(trimmed_all_mol_indices,allmol_coords,allmol_outside_bools_single):
        #Only wrap if all outside
        #NOTE: if water molecule has gone even further (to next box) then currently this code doesn't wrap it completely
        if mol_outside_bool:
            out_cols = np.where(abs(member_coords[0]) > boxlength_half)[0]
            for c in out_cols:
                if member_coords[0][c] > 0:
                    allcoords[members, c] -= boxlength + boxlength * (abs(member_coords[0][c]) // (boxlength * 1.5))
                elif member_coords[0][c] < 0:
                    allcoords[members, c] += boxlength + boxlength * (abs(member_coords[0][c]) // (boxlength * 1.5))
    #print(f"FinalTime:{time.time()-checkpoint}")
    return allcoords

#Function to wrap coordinates of whole molecules outside box
def wrap_box_coords_old3(allcoords,boxlength,connectivity_dict,connectivity):
    #checkpoint = time.time()
    boxlength_half=boxlength/2
    #Get atom indices for atoms that have a x,y or z coordinate outside box
    mask = np.any(np.abs(allcoords) > boxlength_half, axis=1)
    indices = np.where(mask)[0]
    #print(f"1Time:{time.time()-checkpoint}")
    #20488
    #Get indices of all whole molecules
    all_mol_indices = [connectivity[connectivity_dict.get(i)] for i in indices]
    #print(f"1aTime:{time.time()-checkpoint}")
    #print(all_mol_indices)
    #print(all_mol_indices[3399])
    #Removing duplicates
    #print(f"1bTime:{time.time()-checkpoint}")
    trimmed_all_mol_indices = trim_list_of_lists(all_mol_indices)
    #print("len trimmed_all_mol_indices:", len(trimmed_all_mol_indices))
    #Get all coordinates
    #print(f"2Time:{time.time()-checkpoint}")
    allmol_coords = np.take(allcoords, trimmed_all_mol_indices, axis=0)
    #print(f"3Time:{time.time()-checkpoint}")
    #print(allmol_coords)
    #Check if all members are outside
    allmol_outside_bools = [np.any(np.abs(m) > boxlength_half, axis=1) for m in allmol_coords]
    #print(allmol_outside_bools)
    #print(allmol_outside_bools)
    #allmol_outside_bools_single = [np.all(j) for j in allmol_outside_bools]
    #print(f"4Time:{time.time()-checkpoint}")
    allmol_outside_bools_single = [np.all(j) for j in allmol_outside_bools]
    
    #print("allmol_outside_bools_single:", allmol_outside_bools_single)
    #print(len(allmol_outside_bools_single))
    outside_mol_indices=[i for i, x in enumerate(allmol_outside_bools_single) if x]
    
    #print("len outside_mol_indices:", len(outside_mol_indices))

    #exit()
    #print(allmol_outside_bools_single)
    #exit()
    #print(f"5Time:{time.time()-checkpoint}")
    #allmol_outside_cols = [np.any(np.abs(m) > boxlength_half, axis=1) for m in allmol_coords]
    allmol_outside_cols = [np.where(abs(member_coords[0]) > boxlength_half)[0] for member_coords in allmol_coords]
    #print("allmol_outside_cols:", allmol_outside_cols)
    #exit()
    #Looping over indices
    #print(f"6Time:{time.time()-checkpoint}")
    #for members,member_coords,mol_outside_bool,out_cols in zip(trimmed_all_mol_indices,allmol_coords,allmol_outside_bools_single,allmol_outside_cols):
    #Looping over molindices outside box
    for outmolindex in outside_mol_indices:
        members = trimmed_all_mol_indices[outmolindex]
        member_coords = allmol_coords[outmolindex]
        for c in allmol_outside_cols[outmolindex]:
            colval=member_coords[0][c]
            if colval > 0:
                allcoords[members, c] -= boxlength + boxlength * (abs(colval) // (boxlength * 1.5))
            elif colval < 0:
                allcoords[members, c] += boxlength + boxlength * (abs(colval) // (boxlength * 1.5))
    #print(f"FinalTime:{time.time()-checkpoint}")
    return allcoords

def trim_list_of_lists(k):
    k = sorted(k)
    return np.array(list((k for k, _ in itertools.groupby(k))))
