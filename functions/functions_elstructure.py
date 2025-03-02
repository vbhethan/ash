import numpy as np
import math
import shutil
import os
import glob
import copy
import subprocess as sp

#import ash
import ash.constants
import ash.modules.module_coords
import ash.dictionaries_lists
from ash.functions.functions_general import ashexit, isodd, print_line_with_mainheader,pygrep
from ash.interfaces.interface_ORCA import ORCATheory, run_orca_plot, make_molden_file_ORCA
from ash.modules.module_coords import nucchargelist
from ash.dictionaries_lists import eldict
from ash.constants import hartokcal
from ash.interfaces.interface_multiwfn import multiwfn_run

#CM5. from https://github.com/patrickmelix/CM5-calculator/blob/master/cm5calculator.py

#data from paper for element 1-118
_radii = np.array([0.32, 0.37, 1.30, 0.99, 0.84, 0.75,
          0.71, 0.64, 0.60, 0.62, 1.60, 1.40,
          1.24, 1.14, 1.09, 1.04, 1.00, 1.01,
          2.00, 1.74, 1.59, 1.48, 1.44, 1.30,
          1.29, 1.24, 1.18, 1.17, 1.22, 1.20,
          1.23, 1.20, 1.20, 1.18, 1.17, 1.16,
          2.15, 1.90, 1.76, 1.64, 1.56, 1.46,
          1.38, 1.36, 1.34, 1.30, 1.36, 1.40,
          1.42, 1.40, 1.40, 1.37, 1.36, 1.36,
          2.38, 2.06, 1.94, 1.84, 1.90, 1.88,
          1.86, 1.85, 1.83, 1.82, 1.81, 1.80,
          1.79, 1.77, 1.77, 1.78, 1.74, 1.64,
          1.58, 1.50, 1.41, 1.36, 1.32, 1.30,
          1.30, 1.32, 1.44, 1.45, 1.50, 1.42,
          1.48, 1.46, 2.42, 2.11, 2.01, 1.90,
          1.84, 1.83, 1.80, 1.80, 1.73, 1.68,
          1.68, 1.68, 1.65, 1.67, 1.73, 1.76,
          1.61, 1.57, 1.49, 1.43, 1.41, 1.34,
          1.29, 1.28, 1.21, 1.22, 1.36, 1.43,
          1.62, 1.75, 1.65, 1.57])


_Dz = np.array([0.0056, -0.1543, 0.0000, 0.0333, -0.1030, -0.0446,
      -0.1072, -0.0802, -0.0629, -0.1088, 0.0184, 0.0000,
      -0.0726, -0.0790, -0.0756, -0.0565, -0.0444, -0.0767,
       0.0130, 0.0000, 0.0000, 0.0000, 0.0000, 0.0000,
       0.0000, 0.0000, 0.0000, 0.0000, 0.0000, 0.0000,
      -0.0512, -0.0557, -0.0533, -0.0399, -0.0313, -0.0541,
       0.0092, 0.0000, 0.0000, 0.0000, 0.0000, 0.0000,
       0.0000, 0.0000, 0.0000, 0.0000, 0.0000, 0.0000,
      -0.0361, -0.0393, -0.0376, -0.0281, -0.0220, -0.0381,
       0.0065, 0.0000, 0.0000, 0.0000, 0.0000, 0.0000,
       0.0000, 0.0000, 0.0000, 0.0000, 0.0000, 0.0000,
       0.0000, 0.0000, 0.0000, 0.0000, 0.0000, 0.0000,
       0.0000, 0.0000, 0.0000, 0.0000, 0.0000, 0.0000,
       0.0000, 0.0000, -0.0255, -0.0277, -0.0265, -0.0198,
      -0.0155, -0.0269, 0.0046, 0.0000, 0.0000, 0.0000,
       0.0000, 0.0000, 0.0000, 0.0000, 0.0000, 0.0000,
       0.0000, 0.0000, 0.0000, 0.0000, 0.0000, 0.0000,
       0.0000, 0.0000, 0.0000, 0.0000, 0.0000, 0.0000,
       0.0000, 0.0000, 0.0000, 0.0000, -0.0179, -0.0195,
      -0.0187, -0.0140, -0.0110, -0.0189])

_alpha = 2.474
_C = 0.705
_DHC = 0.0502
_DHN = 0.1747
_DHO = 0.1671
_DCN = 0.0556
_DCO = 0.0234
_DNO = -0.0346


#Get list-of-lists of distances of coords
def distance_matrix_from_coords(coords):
    distmatrix=[]
    for i in coords:
        dist_row=[ash.modules.module_coords.distance(i,j) for j in coords]
        distmatrix.append(dist_row)
    return distmatrix
            


def calc_cm5(atomicNumbers, coords, hirschfeldcharges):
    coords=np.array(coords)
    atomicNumbers=np.array(atomicNumbers)
    #all matrices have the naming scheme matrix[k,k'] according to the paper
    #distances = atoms.get_all_distances(mic=True)
    distances = np.array(distance_matrix_from_coords(coords))
    #print("distances:", distances)
    #atomicNumbers = np.array(atoms.numbers)
    #print("atomicNumbers", atomicNumbers)
    Rz = _radii[atomicNumbers-1]
    RzSum = np.tile(Rz,(len(Rz),1))
    RzSum = np.add(RzSum, np.transpose(RzSum))
    Bkk = np.exp(-_alpha * (np.subtract(distances,RzSum)), out=np.zeros_like(distances), where=distances!=0)
    assert (np.diagonal(Bkk) == 0).all()

    Dz = _Dz[atomicNumbers]
#    Tkk = np.tile(Dz,(len(Dz),1))
#    Tkk = np.subtract(Tkk, np.transpose(Tkk))
    Tkk = np.zeros(shape=Bkk.shape)
    shape = Tkk.shape
    for i in range(shape[0]):
        for j in range(shape[1]):
            numbers = [atomicNumbers[i], atomicNumbers[j]]
            if numbers[0] == numbers[1]:
                continue
            if set(numbers) == set([1,6]):
                Tkk[i,j] = _DHC
                if numbers == [6,1]:
                    Tkk[i,j] *= -1.0
            elif set(numbers) == set([1,7]):
                Tkk[i,j] = _DHN
                if numbers == [7,1]:
                    Tkk[i,j] *= -1.0
            elif set(numbers) == set([1,8]):
                Tkk[i,j] = _DHO
                if numbers == [8,1]:
                    Tkk[i,j] *= -1.0
            elif set(numbers) == set([6,7]):
                Tkk[i,j] = _DCN
                if numbers == [7,6]:
                    Tkk[i,j] *= -1.0
            elif set(numbers) == set([6,8]):
                Tkk[i,j] = _DCO
                if numbers == [8,6]:
                    Tkk[i,j] *= -1.0
            elif set(numbers) == set([7,8]):
                Tkk[i,j] = _DNO
                if numbers == [8,7]:
                    Tkk[i,j] *= -1.0
            else:
                Tkk[i,j] = _Dz[numbers[0]-1] - _Dz[numbers[1]-1]
    assert (np.diagonal(Tkk) == 0).all()
    product = np.multiply(Tkk, Bkk)
    assert (np.diagonal(product) == 0).all()
    result = np.sum(product,axis=1)
    #print("hirschfeldcharges:", hirschfeldcharges)
    #print("result:", result)
    #print(type(result))
    return np.array(hirschfeldcharges) + result


#Read cubefile.
#TODO: Clean up!
def read_cube (cubefile):
    bohrang = 0.52917721067
    LargePrint = True
    #Opening orbital cube file
    try:
        filename = cubefile
        a = open(filename,"r")
        print("Reading orbital file:", filename)
        filebase=os.path.splitext(filename)[0]
    except IndexError:
        print("error")
        quit()
    #Read cube file and get all data. Square values
    count = 0
    grabpoints = False
    grab_deset_id=False #Whether to grab line with DSET_IDs or not
    d = []
    vals=[]
    elems=[]
    molcoords=[]
    molcoords_ang=[]
    numatoms=0
    for line in a:
        count += 1
        words = line.split()
        numwords=len(words)
        #Grabbing origin
        if count == 3:
            #Getting possibly signed numatoms 
            numat_orig=int(line.split()[0])
            if numat_orig < 0:
                #If negative then we have an ID line later with DSET_IDS
                grab_deset_id=True
            numatoms=abs(int(line.split()[0]))
            orgx=float(line.split()[1])
            orgy=float(line.split()[2])
            orgz=float(line.split()[3])
            rlowx=orgx;rlowy=orgy;rlowz=orgz
        if count == 4:
            nx=int(line.split()[0])
            dx=float(line.split()[1])
        if count == 5:
            ny=int(line.split()[0])
            dy=float(line.split()[2])
        if count == 6:
            nz=int(line.split()[0])
            dz=float(line.split()[3])
        #Grabbing molecular coordinates
        if count > 6 and count <= 6+numatoms:
            elems.append(int(line.split()[0]))
            molcoord=[float(line.split()[2]),float(line.split()[3]),float(line.split()[4])]
            molcoord_ang=[bohrang*float(line.split()[2]),bohrang*float(line.split()[3]),bohrang*float(line.split()[4])]
            molcoords.append(molcoord)
            molcoords_ang.append(molcoord_ang)
        # reading gridpoints
        if grabpoints == True:
            b = line.rstrip('\n').replace('  ', ' ').replace('  ', ' ').split(' ')
            b=list(filter(None, b))
            c =[float(i) for i in b]

            if len(c) >0:
                vals.append(c)
        # when to begin reading gridpoints
        if grab_deset_id is True and count == 7+numatoms:
            DSET_IDS_1 = int(line.split()[0])
            DSET_IDS_2 = int(line.split()[1])
        if (count >= 6+numatoms and grabpoints==False and grab_deset_id is False):
            #Setting grabpoints to True for next line
            grabpoints = True
        if (count >= 7+numatoms and grabpoints==False):
            #Now setting grabpoints to True for grabbing next
            grabpoints = True
    if LargePrint==True:
        print("Number of orb/density points:", len(vals))
    finaldict={'rlowx':rlowx,'dx':dx,'nx':nx,'orgx':orgx,'rlowy':rlowy,'dy':dy,'ny':ny,'orgy':orgy,'rlowz':rlowz,'dz':dz,'nz':nz,'orgz':orgz,'elems':elems,
        'molcoords':molcoords,'molcoords_ang':molcoords_ang,'numatoms':numatoms,'filebase':filebase,'vals':vals}
    if grab_deset_id is True:
        #In case we use it later
        finaldict['DSET_IDS_1']=DSET_IDS_1
        finaldict['DSET_IDS_2']=DSET_IDS_2
    return  finaldict

#Subtract one Cube-file from another
def write_cube_diff(cubedict1,cubedict2, name="Default"):

    #Note: For now ignoring DSET_IDS_1 lines that may have been grabbed and present in dicts

    numatoms=cubedict1['numatoms']
    orgx=cubedict1['orgx']
    orgy=cubedict1['orgy']
    orgz=cubedict1['orgz']
    nx=cubedict1['nx']
    dx=cubedict1['dx']
    ny=cubedict1['ny']
    dy=cubedict1['dy']
    nz=cubedict1['nz']
    dz=cubedict1['dz']
    elems=cubedict1['elems']
    molcoords=cubedict1['molcoords']
    val1=cubedict1['vals']
    val2=cubedict2['vals']
    #name=cubedict['name']

    with open(name+".cube", 'w') as file:
        file.write("Cube file generated by ASH\n")
        file.write("Density difference\n")
        file.write("{:>5}   {:9.6f}   {:9.6f}   {:9.6f}\n".format(numatoms,orgx,orgy,orgz))
        file.write("{:>5}   {:9.6f}   {:9.6f}   {:9.6f}\n".format(nx,dx,0.0,0.0))
        file.write("{:>5}   {:9.6f}   {:9.6f}   {:9.6f}\n".format(ny,0.0,dy,0.0))
        file.write("{:>5}   {:9.6f}   {:9.6f}   {:9.6f}\n".format(nz,0.0,0.0,dz))
        for el,c in zip(elems,molcoords):
            file.write("{:>5}   {:9.6f}   {:9.6f}   {:9.6f}   {:9.6f}\n".format(el,el,c[0],c[1],c[2]))
        for v1,v2 in zip(val1,val2):
            diff = [i-j for i,j in zip(v1,v2)]

            if len(v1) == 6:
                file.write("   {:.6e}   {:.6e}   {:.6e}   {:.6e}   {:.6e}   {:.6e}\n".format(diff[0],diff[1],diff[2],diff[3],diff[4],diff[5]))
            elif len(v1) == 5:
                file.write("   {:.6e}   {:.6e}   {:.6e}   {:.6e}   {:.6e}\n".format(diff[0],diff[1],diff[2],diff[3],diff[4]))
            elif len(v1) == 4:
                file.write("   {:.6e}   {:.6e}   {:.6e}   {:.6e}\n".format(diff[0],diff[1],diff[2],diff[3]))
            elif len(v1) == 3:
                file.write("   {:.6e}   {:.6e}   {:.6e}\n".format(diff[0],diff[1],diff[2]))
            elif len(v1) == 2:
                file.write("   {:.6e}   {:.6e}\n".format(diff[0],diff[1]))
            elif len(v1) == 1:
                file.write("   {:.6e}\n".format(diff[0]))


#Sum of 2 Cube-files
def write_cube_sum(cubedict1,cubedict2, name="Default"):
    #Note: For now ignoring DSET_IDS_1 lines that may have been grabbed and present in dicts
    numatoms=cubedict1['numatoms']
    orgx=cubedict1['orgx']
    orgy=cubedict1['orgy']
    orgz=cubedict1['orgz']
    nx=cubedict1['nx']
    dx=cubedict1['dx']
    ny=cubedict1['ny']
    dy=cubedict1['dy']
    nz=cubedict1['nz']
    dz=cubedict1['dz']
    elems=cubedict1['elems']
    molcoords=cubedict1['molcoords']
    val1=cubedict1['vals']
    val2=cubedict2['vals']
    #name=cubedict['name']

    with open(name+".cube", 'w') as file:
        file.write("Cube file generated by ASH\n")
        file.write("Sum of cube-files\n")
        file.write("{:>5}   {:9.6f}   {:9.6f}   {:9.6f}\n".format(numatoms,orgx,orgy,orgz))
        file.write("{:>5}   {:9.6f}   {:9.6f}   {:9.6f}\n".format(nx,dx,0.0,0.0))
        file.write("{:>5}   {:9.6f}   {:9.6f}   {:9.6f}\n".format(ny,0.0,dy,0.0))
        file.write("{:>5}   {:9.6f}   {:9.6f}   {:9.6f}\n".format(nz,0.0,0.0,dz))
        for el,c in zip(elems,molcoords):
            file.write("{:>5}   {:9.6f}   {:9.6f}   {:9.6f}   {:9.6f}\n".format(el,el,c[0],c[1],c[2]))
        for v1,v2 in zip(val1,val2):
            cubesum = [i+j for i,j in zip(v1,v2)]

            if len(v1) == 6:
                file.write("   {:.6e}   {:.6e}   {:.6e}   {:.6e}   {:.6e}   {:.6e}\n".format(cubesum[0],cubesum[1],cubesum[2],cubesum[3],cubesum[4],cubesum[5]))
            elif len(v1) == 5:
                file.write("   {:.6e}   {:.6e}   {:.6e}   {:.6e}   {:.6e}\n".format(cubesum[0],cubesum[1],cubesum[2],cubesum[3],cubesum[4]))
            elif len(v1) == 4:
                file.write("   {:.6e}   {:.6e}   {:.6e}   {:.6e}\n".format(cubesum[0],cubesum[1],cubesum[2],cubesum[3]))
            elif len(v1) == 3:
                file.write("   {:.6e}   {:.6e}   {:.6e}\n".format(cubesum[0],cubesum[1],cubesum[2]))
            elif len(v1) == 2:
                file.write("   {:.6e}   {:.6e}\n".format(cubesum[0],cubesum[1]))
            elif len(v1) == 1:
                file.write("   {:.6e}\n".format(cubesum[0]))

#Product of 2 Cube-files
def write_cube_product(cubedict1,cubedict2, name="Default"):
    #Note: For now ignoring DSET_IDS_1 lines that may have been grabbed and present in dicts
    numatoms=cubedict1['numatoms']
    orgx=cubedict1['orgx']
    orgy=cubedict1['orgy']
    orgz=cubedict1['orgz']
    nx=cubedict1['nx']
    dx=cubedict1['dx']
    ny=cubedict1['ny']
    dy=cubedict1['dy']
    nz=cubedict1['nz']
    dz=cubedict1['dz']
    elems=cubedict1['elems']
    molcoords=cubedict1['molcoords']
    val1=cubedict1['vals']
    val2=cubedict2['vals']
    #name=cubedict['name']

    with open(name+".cube", 'w') as file:
        file.write("Cube file generated by ASH\n")
        file.write("Sum of cube-files\n")
        file.write("{:>5}   {:9.6f}   {:9.6f}   {:9.6f}\n".format(numatoms,orgx,orgy,orgz))
        file.write("{:>5}   {:9.6f}   {:9.6f}   {:9.6f}\n".format(nx,dx,0.0,0.0))
        file.write("{:>5}   {:9.6f}   {:9.6f}   {:9.6f}\n".format(ny,0.0,dy,0.0))
        file.write("{:>5}   {:9.6f}   {:9.6f}   {:9.6f}\n".format(nz,0.0,0.0,dz))
        for el,c in zip(elems,molcoords):
            file.write("{:>5}   {:9.6f}   {:9.6f}   {:9.6f}   {:9.6f}\n".format(el,el,c[0],c[1],c[2]))
        for v1,v2 in zip(val1,val2):
            cubeprod = [i*j for i,j in zip(v1,v2)]

            if len(v1) == 6:
                file.write("   {:.6e}   {:.6e}   {:.6e}   {:.6e}   {:.6e}   {:.6e}\n".format(cubeprod[0],cubeprod[1],cubeprod[2],cubeprod[3],cubeprod[4],cubeprod[5]))
            elif len(v1) == 5:
                file.write("   {:.6e}   {:.6e}   {:.6e}   {:.6e}   {:.6e}\n".format(cubeprod[0],cubeprod[1],cubeprod[2],cubeprod[3],cubeprod[4]))
            elif len(v1) == 4:
                file.write("   {:.6e}   {:.6e}   {:.6e}   {:.6e}\n".format(cubeprod[0],cubeprod[1],cubeprod[2],cubeprod[3]))
            elif len(v1) == 3:
                file.write("   {:.6e}   {:.6e}   {:.6e}\n".format(cubeprod[0],cubeprod[1],cubeprod[2]))
            elif len(v1) == 2:
                file.write("   {:.6e}   {:.6e}\n".format(cubeprod[0],cubeprod[1]))
            elif len(v1) == 1:
                file.write("   {:.6e}\n".format(cubeprod[0]))


#Read cubefile. Grabs coords. Calculates density if MO
def create_density_from_orb (cubefile, denswrite=True, LargePrint=True):
    bohrang = 0.52917721067
    #Opening orbital cube file
    try:
        filename = cubefile
        a = open(filename,"r")
        print("Reading orbital file:", filename)
        filebase=os.path.splitext(filename)[0]
    except IndexError:
        print("error")
        quit()
    if denswrite==True:
        #Write orbital density cube file
        output = open(filebase+'-dens.cube', "w")
    #Read cube file and get all data. Square values
    count = 0
    X = False
    d = []
    densvals = []
    orbvals=[]
    elems=[]
    molcoords=[]
    molcoords_ang=[]
    numatoms=0
    for line in a:
        count += 1
        words = line.split()
        numwords=len(words)
        #Grabbing origin
        if count < 3:
            if denswrite==True:
                output.write(line)
        if count == 3:
            numatoms=abs(int(line.split()[0]))
            orgx=float(line.split()[1])
            orgy=float(line.split()[2])
            orgz=float(line.split()[3])
            rlowx=orgx;rlowy=orgy;rlowz=orgz
            if denswrite==True:
                output.write(line)
        if count == 4:
            nx=int(line.split()[0])
            dx=float(line.split()[1])
            if denswrite==True:
                output.write(line)
        if count == 5:
            ny=int(line.split()[0])
            dy=float(line.split()[2])
            if denswrite==True:
                output.write(line)
        if count == 6:
            nz=int(line.split()[0])
            dz=float(line.split()[3])
            if denswrite==True:
                output.write(line)
        #Grabbing molecular coordinates
        if count > 6 and count <= 6+numatoms:
            elems.append(int(line.split()[0]))
            molcoord=[float(line.split()[2]),float(line.split()[3]),float(line.split()[4])]
            molcoord_ang=[bohrang*float(line.split()[2]),bohrang*float(line.split()[3]),bohrang*float(line.split()[4])]
            molcoords.append(molcoord)
            molcoords_ang.append(molcoord_ang)
            if denswrite==True:
                output.write(line)
        # reading gridpoints
        if X == True:
            b = line.rstrip('\n').replace('  ', ' ').replace('  ', ' ').split(' ')
            b=list(filter(None, b))
            c =[float(i) for i in b]
            #print("c is", c)
            #Squaring orbital values to get density
            csq = [q** 2 for q in c]
            dsq = [float('%.5e' % i) for i in csq]
            densvals.append(dsq)
            dbq = [float('%.5e' % i) for i in c]
            orbvals.append(dbq)
        # when to begin reading gridpoints
        if (count > 6 and numwords == 2 and X==False):
            X = True
            if denswrite==True:
                output.write(line)

    # Go through orb and dens list and print out density file
    alldensvalues=[]
    allorbvalues=[]
    for line in densvals:
        columns = ["%13s" % cell for cell in line]
        for val in columns:
            alldensvalues.append(float(val))
        if denswrite==True:
            linep=' '.join( columns)
            output.write(linep+'\n')

    for line in orbvals:
        dolumns = ["%13s" % cell for cell in line]
        for oval in dolumns:
            allorbvalues.append(float(oval))
    if denswrite==True:
        output.close()
        print("Wrote orbital density file as:", filebase+'-dens.cube')
        print("")
    sumdensvalues=sum(i for i in alldensvalues)
    if LargePrint is True:
        print("Sum of density values is:", sumdensvalues)
        print("Number of density values is", len(alldensvalues))
        print("Number of orb values is", len(allorbvalues))
    return rlowx,dx,nx,orgx,rlowy,dy,ny,orgy,rlowz,dz,nz,orgz,alldensvalues,elems,molcoords_ang,numatoms,filebase


def centroid_calc(rlowx,dx,nx,orgx,rlowy,dy,ny,orgy,rlowz,dz,nz,orgz,alldensvalues ):
    #########################################################
    # Calculate centroid.
    ############################################################

    #Largest x,y,z coordinates
    rhighx=rlowx+(dx*(nx-1))
    rhighy=rlowy+(dy*(ny-1))
    rhighz=rlowz+(dz*(nz-1))
    #Lowest and highest density values
    rlowv = min(float(s) for s in alldensvalues)
    rhighv = max(float(s) for s in alldensvalues)

    sumuppos=0.0
    cenxpos=0.0
    cenypos=0.0
    cenzpos=0.0
    vcount=0

    #print ("dx, dy, dz is", dx, dy, dz)
    #print("range of x:", rlowx, rhighx)
    #print("range of y:", rlowy, rhighy)
    #print("range of z:", rlowz, rhighz)

    for i in range(1,nx+1):
        if (orgx+(i-1)*dx)<rlowx or (orgx+(i-1)*dx)>rhighx:
            print("If statement. Look into. x")
            ashexit()
            continue
        for j in range(1,ny+1):
            if (orgy+(j-1)*dy)<rlowy or (orgy+(j-1)*dy)>rhighy:
                print("If statement. Look into. y")
                ashexit()
                continue
            for k in range(1,nz+1):
                if (orgz+(k-1)*dz)<rlowz or (orgz+(k-1)*dz)>rhighz:
                    print("If statement. Look into. z")
                    ashexit()
                    continue
                #print("i,j,k is", i,j,k)
                valtmp=alldensvalues[vcount]
                if valtmp<rlowv or valtmp>rhighv:
                    print("If statement. Look into. v")
                    ashexit()
                    continue
                if valtmp>0:
                    sumuppos=sumuppos+valtmp
                    #print("sumuppos is", sumuppos)
                    cenxpos=cenxpos+(orgx+(i-1)*dx)*valtmp
                    cenypos=cenypos+(orgy+(j-1)*dy)*valtmp
                    cenzpos=cenzpos+(orgz+(k-1)*dz)*valtmp
                    #print("valtmp is", valtmp)
                    #print("-----------------------")
                vcount+=1

    #Final values
    cenxpos=cenxpos/sumuppos
    cenypos=cenypos/sumuppos
    cenzpos=cenzpos/sumuppos
    return cenxpos,cenypos,cenzpos

# MO-DOS PLOT. Multiply MO energies by -1 and sort.
def modosplot(occorbs_alpha,occorbs_beta,hftyp):
    #Defining sticks as -1 times MO energy (eV)
    stk_alpha=[]
    stk_beta=[]
    for j in occorbs_alpha:
        stk_alpha.append(-1*j)
    if hftyp == "UHF":
        for k in occorbs_beta:
            stk_beta.append(-1*k)
        stk_beta.sort()
    stk_alpha.sort()
    return stk_alpha,stk_beta

#Calculate HOMO number from nuclear charge from XYZ-file and total charge
def HOMOnumbercalc(file,charge,mult):
    el=[]
    with open(file) as f:
        for count,line in enumerate(f):
            if count >1:
                el.append(line.split()[0])
    totnuccharge=0
    for e in el:
        atcharge=eldict[e]
        totnuccharge+=atcharge
    numel=totnuccharge-charge
    HOMOnum_a="unset";HOMOnum_b="unset"
    orcaoffset=-1
    if mult == 1:
        #RHF case. HOMO is numel/2 -1
        HOMOnum_a=(numel/2)+orcaoffset
        HOMOnum_b=(numel/2)+orcaoffset
    elif mult > 1:
        #UHF case.
        numunpel=mult-1
        Doubocc=(numel-numunpel)/2
        HOMOnum_a=Doubocc+numunpel+orcaoffset
        HOMOnum_b=Doubocc+orcaoffset
    return int(HOMOnum_a),int(HOMOnum_b)

#Calculate DDEC charges and derive LJ parameters from ORCA.
#Uses Chargemol program
# Uses ORCA to calculate densities of molecule and its free atoms. Uses orca_2mkl to create Molden file and molden2aim to create WFX file from Molden.
# Wfx file is read into Chargemol program for DDEC analysis which radial moments used to compute C6 parameters and radii for Lennard-Jones equation.
def DDEC_calc(elems=None, theory=None, gbwfile=None, numcores=1, DDECmodel='DDEC3', calcdir='DDEC', molecule_charge=None, 
              molecule_spinmult=None, chargemolbinarydir=None):
    #Creating calcdir. Should not exist previously
    try:
        shutil.rmtree(calcdir)
    except:
        pass
    os.mkdir(calcdir)
    os.chdir(calcdir)
    #Copying GBW file to current dir and label as molecule.gbw
    shutil.copyfile('../'+gbwfile, './' + 'molecule.gbw')

    #Finding molden2aim in PATH. Present in ASH (May require compilation)
    ashpath=os.path.dirname(ash.__file__)
    molden2aim=ashpath+"/external/Molden2AIM/src/"+"molden2aim.exe"
    if os.path.isfile(molden2aim) is False:
        print("Did not find {}. Did you compile it ? ".format(molden2aim))
        print("Go into dir:", ashpath+"/external/Molden2AIM/src")
        print("Compile using gfortran or ifort:")
        print("gfortran -O3 edflib.f90 edflib-pbe0.f90 molden2aim.f90 -o molden2aim.exe")
        print("ifort -O3 edflib.f90 edflib-pbe0.f90 molden2aim.f90 -o molden2aim.exe")
        ashexit()
    else:
        print("Found molden2aim.exe: ", molden2aim)
        
    print("Warning: DDEC_calc requires chargemol-binary dir to be present in environment PATH variable.")

    #Finding chargemoldir from PATH in os.path
    PATH=os.environ.get('PATH').split(':')
    print("PATH: ", PATH)
    print("Searching for molden2aim and chargemol in PATH")
    for p in PATH:
        if 'chargemol' in p:
            print("Found chargemol in path line (this dir should contain the executables):", p)
            chargemolbinarydir=p
    
    #Checking if we can proceed
    if chargemolbinarydir is None:
        print("chargemolbinarydir is not defined.")
        print("Please provide path as argument to DDEC_calc or put the location inside the $PATH variable on your Unix/Linux OS.")
        ashexit()

    #Defining Chargemoldir (main dir) as 3-up from binary dir
    var=os.path.split(chargemolbinarydir)[0]
    var=os.path.split(var)[0]
    chargemoldir=os.path.split(var)[0]
    print("Chargemoldir (base directory): ", chargemoldir)
    print("Chargemol binary dir:", chargemolbinarydir)

    if theory is None :
        print("DDEC_calc requires theory, keyword argument")
        ashexit()
    if theory.__class__.__name__ != "ORCATheory":
        print("Only ORCA is supported as theory in DDEC_calc currently")
        ashexit()

    # What DDEC charge model to use. Jorgensen paper uses DDEC3. DDEC6 is the newer recommended chargemodel
    print("DDEC model:", DDECmodel)

    # Serial or parallel version
    if numcores == 1:
        print("Using serial version of Chargemol")
        chargemol=glob.glob(chargemolbinarydir+'/*serial*')[0]
        #chargemol=chargemolbinarydir+glob.glob('*serial*')[0]
    else:
        print("Using parallel version of Chargemol using {} cores".format(numcores))
        #chargemol=chargemolbinarydir+glob.glob('*parallel*')[0]
        chargemol=glob.glob(chargemolbinarydir+'/*parallel*')[0]
        # Parallelization of Chargemol code. 8 should be good.
        os.environ['OMP_NUM_THREADS'] = str(numcores)
    print("Using Chargemoldir executable: ", chargemol)

    #Dictionary for spin multiplicities of atoms
    spindictionary = {'H':2, 'He': 1, 'Li':2, 'Be':1, 'B':2, 'C':3, 'N':4, 'O':3, 'F':2, 'Ne':1, 'Na':2, 'Mg':1, 'Al':2, 'Si':3, 'P':4, 'S':3, 'Cl':2, 'Ar':1, 'K':2, 'Ca':1, 'Sc':2, 'Ti':3, 'V':4, 'Cr':7, 'Mn':6, 'Fe':5, 'Co':4, 'Ni':3, 'Cu':2, 'Zn':1, 'Ga':2, 'Ge':3, 'As':4, 'Se':3, 'Br':2, 'Kr':1, 'Rb':2, 'Sr':1, 'Y':2, 'Zr':3, 'Nb':6, 'Mo':7, 'Tc':6, 'Ru':5, 'Rh':4, 'Pd':1, 'Ag':2, 'Cd':1, 'In':2, 'Sn':3, 'Sb':4, 'Te':3, 'I':2, 'Xe':1, 'Cs':2, 'Ba':1, 'La':2, 'Ce':1, 'Pr':4, 'Nd':5, 'Pm':6, 'Sm':7, 'Eu':8, 'Gd':9, 'Tb':6, 'Dy':5, 'Ho':4, 'Er':3, 'Tm':2, 'Yb':1, 'Lu':2, 'Hf':3, 'Ta':4, 'W':5, 'Re':6, 'Os':5, 'Ir':4, 'Pt':3, 'Au':2, 'Hg':1, 'Tl':2, 'Pb':3, 'Bi':4, 'Po':3, 'At':2, 'Rn':1, 'Fr':2, 'Ra':1, 'Ac':2, 'Th':3, 'Pa':4, 'U':5, 'Np':6, 'Pu':7, 'Am':8, 'Cm':9, 'Bk':6, 'Cf':5, 'Es':5, 'Fm':3, 'Md':2, 'No':1, 'Lr':2, 'Rf':3, 'Db':4, 'Sg':5, 'Bh':6, 'Hs':5, 'Mt':4, 'Ds':3, 'Rg':2, 'Cn':1, 'Nh':2, 'Fl':3, 'Mc':4, 'Lv':3, 'Ts':2, 'Og':1 }

    #Dictionary to keep track of radial volumes
    voldict = {}

    uniqelems=set(elems)
    numatoms=len(elems)

    print("Lennard-Jones parameter creation from ORCA densities")
    print("")
    print("First calculating densities of free atoms")
    print("Will skip calculation if wfx file already exists")
    print("")

    # Calculate elements
    print("------------------------------------------------------------------------")
    for el in uniqelems:
        print("Doing element:", el)

        #Skipping analysis if wfx file exists
        if os.path.isfile(el+'.molden.wfx'):
            print(el+'.molden.wfx', "exists already. Skipping calculation.")
            continue
        #TODO: Revisit with ORCA5 and TRAH?
        scfextrasettingsstring="""%scf
Maxiter 500
DIISMaxIt 0
ShiftErr 0.0000
DampFac 0.8500
DampMax 0.9800
DampErr 0.0300
cnvsoscf false
cnvkdiis false
end"""

        #Creating ORCA object for  element
        ORCASPcalculation = ORCATheory(orcadir=theory.orcadir, orcasimpleinput=theory.orcasimpleinput,
                                           orcablocks=theory.orcablocks, extraline=scfextrasettingsstring)

        #Element coordinates
        Elfrag = ash.Fragment(elems=[el], coords=[[0.0,0.0,0.0]])
        print("Elfrag dict ", Elfrag.__dict__)
        ash.Singlepoint(theory=ORCASPcalculation,fragment=Elfrag, charge=0, mult=spindictionary[el])
        #Preserve outputfile and GBW file for each element
        shutil.copyfile(ORCASPcalculation.filename+'.out', './' + str(el) + '.out')
        shutil.copyfile(ORCASPcalculation.filename+'.gbw', './' + str(el) + '.gbw')

        #Create molden file from el.gbw
        sp.call([theory.orcadir+'/orca_2mkl', el, '-molden'])


        #Cleanup ORCA calc for each element
        ORCASPcalculation.cleanup()

        #Write configuration file for molden2aim
        with open("m2a.ini", 'w') as m2afile:
            string = """########################################################################
        #  In the following 8 parameters,
        #     >0:  always performs the operation without asking the user
        #     =0:  asks the user whether to perform the operation
        #     <0:  always neglect the operation without asking the user
        molden= 1           ! Generating a standard Molden file in Cart. function
        wfn= -1              ! Generating a WFN file
        wfncheck= -1         ! Checking normalization for WFN
        wfx= 1              ! Generating a WFX file (not implemented)
        wfxcheck= 1         ! Checking normalization for WFX (not implemented)
        nbo= -1              ! Generating a NBO .47 file
        nbocheck= -1         ! Checking normalization for NBO's .47
        wbo= -1              ! GWBO after the .47 file being generated

        ########################################################################
        #  Which quantum chemistry program is used to generate the MOLDEN file?
        #  1: ORCA, 2: CFOUR, 3: TURBOMOLE, 4: JAGUAR (not supported),
        #  5: ACES2, 6: MOLCAS, 7: PSI4, 8: MRCC, 9: NBO 6 (> ver. 2014),
        #  0: other programs, or read [Program] xxx from MOLDEN.
        #
        #  If non-zero value is given, [Program] xxx in MOLDEN will be ignored.
        #
        program=1

        ########################################################################
        #  For ECP: read core information from Molden file
        #<=0: if the total_occupation_number is smaller than the total_Za, ask
        #     the user whether to read core information
        # >0: always search and read core information
        rdcore=0

        ########################################################################
        #  Which orbirals will be printed in the WFN/WFX file?
        # =0: print only the orbitals with occ. number > 5.0d-8
        # <0: print only the orbitals with occ. number > 0.1 (debug only)
        # >0: print all the orbitals
        iallmo=0

        ########################################################################
        #  Used for WFX only
        # =0: print "UNKNOWN" for Energy and Virial Ratio
        # .ne. 0: print 0.0 for Energy and 2.0 for Virial Ratio
        unknown=1

        ########################################################################
        #  Print supporting information or not
        # =0: print; .ne. 0: do not print
        nosupp=0

        ########################################################################
        #  The following parameters are used only for debugging.
        clear=1            ! delete temporary files (1) or not (0)

        ########################################################################
        """
            m2afile.write(string)

        #Write settings file
        mol2aiminput=[' ',  el+'.molden.input', 'Y', 'Y', 'N', 'N', ' ', ' ']
        m2aimfile = open("mol2aim.inp", "w")
        for mline in mol2aiminput:
            m2aimfile.write(mline+'\n')
        m2aimfile.close()

        #Run molden2aim
        m2aimfile = open('mol2aim.inp')
        p = sp.Popen(molden2aim, stdin=m2aimfile, stderr=sp.STDOUT)
        p.wait()

        #Write job control file for Chargemol
        wfxfile=el+'.molden.wfx'
        jobcontfilewrite=[
        '<atomic densities directory complete path>',
        chargemoldir+'/atomic_densities/',
        '</atomic densities directory complete path>',
        '<input filename>',
        wfxfile,
        '<charge type>',
        DDECmodel,
        '</charge type>',
        '<compute BOs>',
        '.true.',
        '</compute BOs>',
        ]
        jobfile = open("job_control.txt", "w")
        for jline in jobcontfilewrite:
            jobfile.write(jline+'\n')

        jobfile.close()
        #CALLING chargemol
        sp.call(chargemol)
        print("------------------------------------------------------------------------")


    #DONE WITH ELEMENT CALCS

    print("")
    print("=============================")
    #Getting volumes from output
    for el in uniqelems:
        with open(el+'.molden.output') as momfile:
            for line in momfile:
                if ' The computed Rcubed moments of the atoms' in line:
                    elmom=next(momfile).split()[0]
                    voldict[el] = float(elmom)

        print("Element", el, "is done.")

    print("")
    print("Calculated radial volumes of free atoms (Bohrs^3):", voldict)
    print("")

    #Now doing main molecule. Skipping ORCA calculation since we have copied over GBW file
    # Create molden file
    sp.call(['orca_2mkl', "molecule", '-molden'])

    #Write input for molden2aim
    
    if molecule_charge==0:
        mol2aiminput=[' ',  "molecule"+'.molden.input', str(molecule_spinmult), ' ', ' ', ' ']
    else:
        #Charged system, will ask for charge
        #str(molecule_charge)
        mol2aiminput=[' ',  "molecule"+'.molden.input', 'N', '2', ' ', str(molecule_spinmult), ' ', ' ', ' ']        
        
    m2aimfile = open("mol2aim.inp", "w")
    for mline in mol2aiminput:
        m2aimfile.write(mline+'\n')
    m2aimfile.close()

    #Run molden2aim
    print("Running Molden2Aim for molecule")
    m2aimfile = open('mol2aim.inp')
    p = sp.Popen(molden2aim, stdin=m2aimfile, stderr=sp.STDOUT)
    p.wait()

    # Write job control file for Chargemol
    wfxfile = "molecule" + '.molden.wfx'
    jobcontfilewrite = [
        '<net charge>',
        '{}'.format(str(float(molecule_charge))),
        '</net charge>',
        '<atomic densities directory complete path>',
        chargemoldir + '/atomic_densities/',
        '</atomic densities directory complete path>',
        '<input filename>',
        wfxfile,
        '<charge type>',
        DDECmodel,
        '</charge type>',
        '<compute BOs>',
        '.true.',
        '</compute BOs>',
    ]
    jobfile = open("job_control.txt", "w")
    for jline in jobcontfilewrite:
        jobfile.write(jline+'\n')

    jobfile.close()
    if os.path.isfile("molecule"+'.molden.output') == False:
        sp.call(chargemol)
    else:
        print("Skipping Chargemol step. Output file exists")


    #Grabbing radial moments from output
    molmoms=[]
    grabmoms=False
    with open("molecule"+'.molden.output') as momfile:
        for line in momfile:
            if ' The computed Rfourth moments of the atoms' in line:
                grabmoms=False
                continue
            if grabmoms==True:
                temp=line.split()
                [molmoms.append(float(i)) for i in temp]
            if ' The computed Rcubed moments of the atoms' in line:
                grabmoms=True

    #Grabbing DDEC charges from output
    if DDECmodel == 'DDEC3':
        chargefile='DDEC3_net_atomic_charges.xyz'
    elif DDECmodel == 'DDEC6':
        chargefile='DDEC6_even_tempered_net_atomic_charges.xyz'

    grabcharge=False
    ddeccharges=[]
    with open(chargefile) as chfile:
        for line in chfile:
            if grabcharge==True:
                ddeccharges.append(float(line.split()[5]))
                if int(line.split()[0]) == numatoms:
                    grabcharge=False
            if "atom number, atomic symbol, x, y, z, net_charge," in line:
                grabcharge=True

    print("")
    print("molmoms is", molmoms)
    print("voldict is", voldict)
    print("ddeccharges: ", ddeccharges)
    print("elems: ", elems)
    os.chdir('..')
    return ddeccharges, molmoms, voldict




#Tkatchenko
#alpha_q_m = Phi*Rvdw^7
#https://arxiv.org/pdf/2007.02992.pdf
def Rvdwfree(polz):
    #Fine-structure constant (2018 CODATA recommended value)
    FSC=0.0072973525693
    Phi=FSC**(4/3)
    RvdW=(polz/Phi)**(1/7)
    return RvdW
    

def DDEC_to_LJparameters(elems, molmoms, voldict, scale_polarH=False):
    
    #voldict: Vfree. Computed using MP4SDQ/augQZ and chargemol in Jorgensen paper
    # Testing: Use free atom volumes calculated at same level of theory as molecule
    
    #Rfree fit parameters. Jorgensen 2016 J. Chem. Theory Comput. 2016, 12, 2312−2323. H,C,N,O,F,S,Cl
    #Thes are free atomic vdW radii
    # In Jorgensen and Cole papers these are fit parameters : rfreedict = {'H':1.64, 'C':2.08, 'N':1.72, 'O':1.6, 'F':1.58, 'S':2.0, 'Cl':1.88}
    # We are instead using atomic Rvdw derived directly from atomic polarizabilities
    
    print("Elems:", elems)
    print("Molmoms:", molmoms)
    print("voldict:", voldict)

    #Calculating A_i, B_i, epsilon, sigma, r0 parameters
    Blist=[]
    Alist=[]
    sigmalist=[]
    epsilonlist=[]
    r0list=[]
    Radii_vdw_free=[]
    for count,el in enumerate(elems):
        print("el :", el, "count:", count)
        atmnumber=ash.modules.module_coords.elematomnumbers[el.lower()]
        print("atmnumber:", atmnumber)
        Radii_vdw_free.append(ash.dictionaries_lists.elems_C6_polz[atmnumber].Rvdw_ang)
        print("Radii_vdw_free:", Radii_vdw_free)
        volratio=molmoms[count]/voldict[el]
        print("volratio:", volratio)
        C6inkcal=ash.constants.harkcal*(ash.dictionaries_lists.elems_C6_polz[atmnumber].C6**(1/6)* ash.constants.bohr2ang)**6
        print("C6inkcal:", C6inkcal)
        B_i=C6inkcal*(volratio**2)
        print("B_i:", B_i)
        Raim_i=volratio**(1/3)*ash.dictionaries_lists.elems_C6_polz[atmnumber].Rvdw_ang
        print("Raim_i:", Raim_i)
        A_i=0.5*B_i*(2*Raim_i)**6
        print("A_i:", A_i)
        sigma=(A_i/B_i)**(1/6)
        print("sigma :", sigma)
        r0=sigma*(2**(1/6))
        print("r0:", r0)
        epsilon=(A_i/(4*sigma**12))
        print("epsilon:", epsilon)
        
        sigmalist.append(sigma)
        Blist.append(B_i)
        Alist.append(A_i)
        epsilonlist.append(epsilon)
        r0list.append(r0)

    print("Before corrections:")
    print("elems:", elems)
    print("Radii_vdw_free:", Radii_vdw_free)
    print("Alist is", Alist)
    print("Blist is", Blist)
    print("sigmalist is", sigmalist)
    print("epsilonlist is", epsilonlist)
    print("r0list is", r0list)
    
    #Accounting for polar H. This could be set to zero as in Jorgensen paper
    if scale_polarH is True:
        print("Scaling og polar H not implemented yet")
        ashexit()
        for count,el in enumerate(elems):
            if el == 'H':
                bla=""
                #Check if H connected to polar atom (O, N, S ?)
                #if 'H' connected to polar:
                    #1. Set eps,r0/sigma to 0 if so
                    #2. Add to heavy atom if so
                    #nH = 1
                    #indextofix = 11
                    #hindex = 12
                    #Blist[indextofix] = ((Blist[indextofix]) ** (1 / 2) + nH * (Blist[hindex]) ** (1 / 2)) ** 2

    return r0list, epsilonlist


#Get number of core electrons for list of elements
def num_core_electrons(elems):
    sum=0
    #formula_list = ash.modules.module_coords.molformulatolist(fragment.formula)
    for i in elems:
        cels = ash.dictionaries_lists.atom_core_electrons[i]
        sum+=cels
    return sum


#Check if electrons pairs in element list are less than numcores. Reduce numcores if so.
#Using even number of electrons
def check_cores_vs_electrons(elems,numcores,charge):
    print("numcores:", numcores)
    print("charge:", charge)
    numelectrons = int(nucchargelist(elems) - charge)
    #Reducing numcores if fewer active electron pairs than numcores.
    core_electrons = num_core_electrons(elems)
    print("core_electrons:", core_electrons)
    valence_electrons = (numelectrons - core_electrons)
    electronpairs = int(valence_electrons / 2)
    if electronpairs  < numcores:
        print("Number of total electrons :", numelectrons)
        print("Number of valence electrons :", valence_electrons )
        print("Number of valence electron pairs :", electronpairs )
        if isodd(electronpairs):
            if electronpairs > 1:
                #Changed from subtracting 1 to 3 after DLPNO-CC of NaH calculation failed (MB16-43)
                numcores=electronpairs-3
            else:
                numcores=electronpairs
        else:
            numcores=electronpairs
    if numcores == 0:
        numcores=1
    print("Setting numcores to:", numcores)
    return numcores



#Approximate J-coupling spin projection functions
def Jcoupling_Yamaguchi(HSenergy,BSenergy,HS_S2,BS_S2):
    print("Yamaguchi spin projection")
    J=-1*(HSenergy-BSenergy)/(HS_S2-BS_S2)
    J_kcal=J*ash.constants.harkcal
    J_cm=J*ash.constants.hartocm
    print("J coupling constant: {} Eh".format(J))
    print("J coupling constant: {} kcal/Mol".format(J_kcal))
    print("J coupling constant: {} cm**-1".format(J_cm))            
    return J
#Strong-interaction limit (bond-formation)
def Jcoupling_Bencini(HSenergy,BSenergy,smax):
    print("Bencini spin projection")
    J=-1*(HSenergy-BSenergy)/(smax*(smax+1))
    J_kcal=J*ash.constants.harkcal
    J_cm=J*ash.constants.hartocm
    print("Smax : ", smax)
    print("J coupling constant: {} Eh".format(J))
    print("J coupling constant: {} kcal/Mol".format(J_kcal))
    print("J coupling constant: {} cm**-1".format(J_cm))
    return J
#Weak-interaction limit
def Jcoupling_Noodleman(HSenergy,BSenergy,smax):
    print("Noodleman spin projection")
    J=-1*(HSenergy-BSenergy)/(smax)**2
    J_kcal=J*ash.constants.harkcal
    J_cm=J*ash.constants.hartocm
    print("Smax : ", smax)
    print("J coupling constant: {} Eh".format(J))
    print("J coupling constant: {} kcal/Mol".format(J_kcal))
    print("J coupling constant: {} cm**-1".format(J_cm))
    return J

#Select an active space from list of occupations and thresholds
def select_space_from_occupations(occlist, selection_thresholds=[1.98,0.02]):
    upper_threshold=selection_thresholds[0]
    lower_threshold=selection_thresholds[1]
    welloccorbs=[i for i in occlist if i < upper_threshold and i > lower_threshold]
    numelectrons=round(sum(welloccorbs))
    numorbitals=len(welloccorbs)
    return [numelectrons,numorbitals]

# Interface to XDM postg program
#https://github.com/aoterodelaroza/postg
def xdm_run(wfxfile=None, postgdir=None,a1=None, a2=None,functional=None):

    if postgdir == None:
        # Trying to find postgdir in path
        print("postgdir keyword argument not provided to xdm_run. Trying to find postg in PATH")
        try:
            postgdir = os.path.dirname(shutil.which('postg'))
            print("Found postg in path. Setting postgdir.")
        except:
            print("Found no postg executable in path. Exiting... ")
            ashexit()

    parameterdict= {'pw86pbe' : [0.7564,1.4545], 'b3lyp' : [0.6356, 1.5119],
    'b3pw91' : [0.6002,1.4043], 'b3p86' : [1.0400, 0.3741], 'pbe0' : [0.4186,2.6791],
    'camb3lyp' : [0.3248,2.8607], 'b97-1' : [0.1998,3.5367], 'bhandhlyp' : [0.5610, 1.9894],
    'blyp' : [0.7647,0.8457],'pbe' : [0.4492,2.5517],'lcwpbe' : [1.0149, 0.6755],
    'tpss' : [0.6612, 1.5111], 'b86bpbe' : [0.7443, 1.4072]}

    if a1 == None or a2 == None:
        print("a1/a2 parameters not given. Looking up functional in table")
        print("Parameter table:", parameterdict)
        a1, a2 = parameterdict[functional.lower()]
        print(f"XDM a1: {a1}, a2: {a2}")
    with open('xdm-postg.out', 'w') as ofile:
        process = sp.run([postgdir+'/postg', str(a1), str(a2), str(wfxfile), str(functional) ], check=True, 
            stdout=ofile, stderr=ofile, universal_newlines=True)

    dispgrab=False
    dispgradient=[]
    with open('xdm-postg.out', 'r') as xdmfile:
        for line in xdmfile:
            #TODO: Grab Hirshfeld charges
            #TODO: C6,C8, C10 coefficients, moments and volumes
            if 'dispersion energy' in line:
                dispenergy = float(line.split()[-1])
            if 'dispersion force constant matrix' in line:
                dispgrab=False
            if dispgrab == True:
                if '#' not in line:
                    grad_x=-1*float(line.split()[1])
                    grad_y=-1*float(line.split()[2])
                    grad_z=-1*float(line.split()[3])
                    dispgradient.append([grad_x,grad_y,grad_z])
            if 'dispersion forces' in line:
                dispgrab=True

    dispgradient=np.array(dispgradient)
    print("dispenergy:", dispenergy)
    print("dispgradient:", dispgradient)
    return dispenergy, dispgradient

#Create difference density for 2 calculations differing in either fragment or theory-level
def difference_density_ORCA(fragment_A=None, fragment_B=None, theory_A=None, theory_B=None, griddensity=80, cubefilename='difference_density'):
    print_line_with_mainheader("difference_density_ORCA")
    print("Will calculate and create a difference density for molecule")
    print("Either fragment can be different (different geometry, different charge, different spin)")
    print("Or theory can be different (different functional, different basis set)")
    print()
    print("griddensity:", griddensity)

    if fragment_A is None or fragment_B is None:
        print("You need to provide an ASH fragment for both fragment_A and fragment_B (can be the same)")
        ashexit()
    if fragment_A.charge == None or fragment_B.charge == None:
        print("You must provide charge/multiplicity information in all fragments")
        ashexit()
    if theory_A == None or theory_A.__class__.__name__ != "ORCATheory":
        print("theory_A: You must provide an ORCATheory level")
        ashexit()
    if theory_B == None or theory_B.__class__.__name__ != "ORCATheory":
        print("theory_B: You must provide an ORCATheory level")
        ashexit()

    #------------------
    #Calculation 1
    #------------------
    theory_A.filename="calc_A"
    result_calc1=ash.Singlepoint(theory=theory_A, fragment=fragment_A)
    #Run orca_plot to request electron density creation from ORCA gbw file
    run_orca_plot("calc_A.gbw", "density", gridvalue=griddensity)


    #------------------
    #Calculation 2
    #------------------
    theory_B.filename="calc_B"
    result_calc2=ash.Singlepoint(theory=theory_B, fragment=fragment_B)
    #Run orca_plot to request electron density creation from ORCA gbw file
    run_orca_plot("calc_B.gbw", "density", gridvalue=griddensity)

    #Read Cubefiles from disk
    cube_data1 = read_cube("calc_A.eldens.cube")
    cube_data2 = read_cube("calc_B.eldens.cube")

    #Write out difference density as a Cubefile
    write_cube_diff(cube_data2, cube_data1, cubefilename)
    print()
    print(f"Difference density (B - A) file was created: {cubefilename}.cube")


#Create deformation density and do NOCV analysis by providing fragment files for AB, A and B and a theory-level object.
#TODO: Limitation, ORCA can only do closed-shell case
#TODO: Switch to multiwfn for more generality
def NOCV_density_ORCA(fragment_AB=None, fragment_A=None, fragment_B=None, theory=None, griddensity=80,
                            NOCV=True, num_nocv_pairs=5, keep_all_orbital_cube_files=False,
                            make_cube_files=True):
    print_line_with_mainheader("NOCV_density_ORCA")
    print("Will calculate and create a deformation density for molecule AB for fragments A and B")
    print("griddensity:", griddensity)
    print("NOCV option:", NOCV)
    if NOCV is True:
        print("Will do NOCV analysis on AB fragment deformation density using A+B promolecular density")
    else:
        print("Full NOCV analysis not carried out")
    #Early exits
    if fragment_AB is None or fragment_A is None or fragment_B is None:
        print("You need to provide an ASH fragment")
        ashexit()
    if fragment_AB.charge == None or fragment_A.charge == None or fragment_B.charge == None:
        print("You must provide charge/multiplicity information to all fragments")
        ashexit()
    if theory == None or theory.__class__.__name__ != "ORCATheory":
        print("You must provide an ORCATheory level")
        ashexit()
    
    #Creating copies of theory object provided
    calc_AB = copy.copy(theory); calc_AB.filename="calcAB"
    calc_A = copy.copy(theory); calc_A.filename="calcA"
    calc_B = copy.copy(theory); calc_B.filename="calcB"

    #-------------------------
    #Calculation on A
    #------------------------
    print("-"*120)
    print("Performing ORCA calculation on fragment A")
    print("-"*120)
    #Run A SP
    result_calcA=ash.Singlepoint(theory=calc_A, fragment=fragment_A)
    #Run orca_plot to request electron density creation from ORCA gbw file
    if make_cube_files is True:
        run_orca_plot("calcA.gbw", "density", gridvalue=griddensity)

    #-------------------------
    #Calculation on B
    #------------------------
    print()
    print("-"*120)
    print("Performing ORCA calculation on fragment B")
    print("-"*120)
    #Run B SP
    result_calcB=ash.Singlepoint(theory=calc_B, fragment=fragment_B)
    #Run orca_plot to request electron density creation from ORCA gbw file
    if make_cube_files is True:
        run_orca_plot("calcB.gbw", "density", gridvalue=griddensity)


    #-----------------------------------------
    # merge A + B to get promolecular density
    #-----------------------------------------
    print()
    print("-"*120)
    print("Using orca_mergefrag to combine GBW-files for A and B into AB promolecule file: promolecule_AB.gbw")
    print("-"*120)
    p = sp.run(['orca_mergefrag', "calcA.gbw", "calcB.gbw", "promolecule_AB.gbw"], encoding='ascii')

    #NOTE: promolecule_AB.gbw here contains orbitals that have not been orthogonalize
    #Here we run a Noiter job to orthogonalize
    promolecule_AB_orthog = copy.copy(theory)
    promolecule_AB_orthog.filename="calcAB"
    promolecule_AB_orthog.orcasimpleinput+=" noiter"
    promolecule_AB_orthog.moreadfile="promolecule_AB.gbw"
    promolecule_AB_orthog.orcablocks="%scf guessmode fmatrix end"
    promolecule_AB_orthog.filename="promol"
    promolecule_AB_orthog.keep_last_output=False
    print()
    print("-"*120)
    print("Performing ORCA noiter calculation in order to orthogonalize orbitals and get file: promolecule_AB_orthog.gbw")
    print("-"*120)
    result_promol=ash.Singlepoint(theory=promolecule_AB_orthog, fragment=fragment_AB)
    #NOTE: calc_promol.gbw will contain  orthogonalized orbitals
    #Writing out electron density of orthogonalized promolecular electron density
    print()
    if make_cube_files is True:
        print("-"*120)
        print("Performing orca_plot calculation to create density Cubefile: promolecule_AB_orthogonalized.eldens.cube")
        print("-"*120)
        run_orca_plot(promolecule_AB_orthog.filename+".gbw", "density", gridvalue=80)
        os.rename(f"{promolecule_AB_orthog.filename}.eldens.cube","promolecule_AB_orthogonalized.eldens.cube")

    #----------------------------
    #Calculation on AB with NOCV
    #----------------------------
    #Run AB SP
    if NOCV is True:
        print()
        print("NOCV option on. Note that if system is open-shell then ORCA will not perform NOCV")
        calc_AB.orcablocks = calc_AB.orcablocks + """
%scf
EDA true
guessmode fmatrix
end
"""
        calc_AB.moreadfile="promolecule_AB.gbw"
    print()
    print("-"*120)
    print("Calling ORCA to perform calculation on AB")
    print("-"*120)
    result_calcAB=ash.Singlepoint(theory=calc_AB, fragment=fragment_AB)
    if make_cube_files is True:
        #Run orca_plot to request electron density creation from ORCA gbw file
        run_orca_plot("calcAB.gbw", "density", gridvalue=griddensity)

        #-----------------------------------------
        # Make deformation density as difference
        #-----------------------------------------

        #Read Cubefiles from disk
        print()
        print("-"*120)
        print("Reading Cubefiles and creating difference density (i.e. deformation density) from orthogonalized promolecular density and final density")
        print("-"*120)
        cube_data1 = read_cube("promolecule_AB_orthogonalized.eldens.cube")
        cube_data2 = read_cube(f"calcAB.eldens.cube")

        #Write out difference density as a Cubefile
        write_cube_diff(cube_data2, cube_data1, "full_deformation_density")
        print()
        print("Deformation density file was created: full_deformation_density.cube")
        print()


    #If nocv GBW file is present then NOCV was definitely carried out and we can calculate cube files of the donor-acceptor orbitals
    if os.path.isfile("calcAB.nocv.gbw") is False:
        print("No NOCV file was created by ORCA. This probably means that ORCA could not perform the NOCV calculation.")
        print("Possibly as the system is open-shell.")
        return

    #FURTHER
    print ("NOCV analysis was carried out, see calcAB.out for details")
    print()
    print("-"*120)
    print("Running dummy ORCA noiter PrintMOS job using NOCV orbitals in file: calcAB.nocv.gbw ")
    print("-"*120)
    #Creating noiter ORCA output for visualization in Chemcraft
    calc_AB.orcasimpleinput+=" noiter printmos printbasis"
    calc_AB.moreadfile="calcAB.nocv.gbw"
    calc_AB.orcablocks=""
    calc_AB.filename="NOCV-noiter-visualization"
    calc_AB.keep_last_output=False
    result_calcAB_noiter=ash.Singlepoint(theory=calc_AB, fragment=fragment_AB)

    print()
    if make_cube_files is True:
        #Creating Cube files
        print("Now creating Cube files for main NOCV pairs and making orbital-pair deformation densities")
        print("Creating Cube file for NOCV total deformation density:")
        run_orca_plot("calcAB.nocv.gbw", "density", gridvalue=griddensity)
        os.rename(f"calcAB.nocv.eldens.cube", f"NOCV-total-density.cube")
        num_mos=int(pygrep("Number of basis functions                   ...", "calcAB.out")[-1])
        
        #Storing individual NOCV MOs and densities in separate dir (less useful)
        print("-"*120)
        print("Creating final Cube files for NOCV pair orbitals, orbital-densities and orbital-pair deformation densities")
        print("-"*120)
        try:
            os.mkdir("NOCV_orbitals_and_densities")
        except:
            pass
        for i in range(0,num_nocv_pairs):
            print("-----------------------")
            print(f"Now doing NOCV pair: {i}")
            print("-----------------------")
            print()
            print("Creating Cube file for NOCV donor MO number:", i)
            run_orca_plot("calcAB.nocv.gbw", "mo", mo_number=i, gridvalue=griddensity)
            os.rename(f"calcAB.nocv.mo{i}a.cube", f"calcAB.NOCVpair_{i}.donor_mo{i}a.cube")
            print("Creating density for orbital")
            create_density_from_orb (f"calcAB.NOCVpair_{i}.donor_mo{i}a.cube", denswrite=True, LargePrint=True)
            
            print("Creating Cube file for NOCV acceptor MO number:", num_mos-1-i)
            run_orca_plot("calcAB.nocv.gbw", "mo", mo_number=num_mos-1-i, gridvalue=griddensity)
            os.rename(f"calcAB.nocv.mo{num_mos-1-i}a.cube", f"calcAB.NOCVpair_{i}.acceptor_mo{num_mos-1-i}a.cube")
            print("Creating density for orbital")
            create_density_from_orb (f"calcAB.NOCVpair_{i}.acceptor_mo{num_mos-1-i}a.cube", denswrite=True, LargePrint=False)
            
            #Difference density for orbital pair
            donor = read_cube(f"calcAB.NOCVpair_{i}.donor_mo{i}a-dens.cube")
            acceptor = read_cube(f"calcAB.NOCVpair_{i}.acceptor_mo{num_mos-1-i}a-dens.cube")
            print(f"Making difference density file: NOCV_pair_{i}_deform_density.cube")
            write_cube_diff(acceptor,donor, name=f"NOCV_pair_{i}_deform_density")
            
            #Move less important stuff to dir
            os.rename(f"calcAB.NOCVpair_{i}.donor_mo{i}a.cube",f"NOCV_orbitals_and_densities/calcAB.NOCVpair_{i}.donor_mo{i}a.cube")
            os.rename(f"calcAB.NOCVpair_{i}.acceptor_mo{num_mos-1-i}a.cube",f"NOCV_orbitals_and_densities/calcAB.NOCVpair_{i}.acceptor_mo{num_mos-1-i}a.cube")
            os.rename(f"calcAB.NOCVpair_{i}.donor_mo{i}a-dens.cube",f"NOCV_orbitals_and_densities/calcAB.NOCVpair_{i}.donor_mo{i}a-dens.cube")
            os.rename(f"calcAB.NOCVpair_{i}.acceptor_mo{num_mos-1-i}a-dens.cube",f"NOCV_orbitals_and_densities/calcAB.NOCVpair_{i}.acceptor_mo{num_mos-1-i}a-dens.cube")
        #Optionally delete whole directory at end
        if keep_all_orbital_cube_files is False:
            print("keep_all_orbital_cube_files option is False")
            print("Deleting directory: NOCV_orbitals_and_densities")
            shutil.rmtree("NOCV_orbitals_and_densities")
        print()
    ###############################
    # FINAL EDA analysis printout

    deltaE_int=(result_calcAB.energy - result_calcA.energy - result_calcB.energy)*hartokcal
    deltaE_orb=float(pygrep("Delta Total Energy  (Kcal/mol) :","calcAB.out")[-1])
    deltaE_steric=deltaE_int-deltaE_orb #Elstat+Pauli. Further ecomposition not possibly at the moment

    print("="*20)
    print("Basic EDA analysis")
    print("="*20)
    print()
    print("-"*50)
    print(f"{'dE(steric)':<20s} {deltaE_steric:>14.3f} kcal/mol")
    print(f"{'dE(orb)':<20s} {deltaE_orb:>14.3f} kcal/mol")
    print(f"{'dE(int)':<20s} {deltaE_int:>14.3f} kcal/mol")
    print("-"*50)
    print("E(steric) is sum of electrostatic and Pauli repulsion")
    print("dE(orb) is the NOCV-ETS orbital-relaxation of orthogonalized promolecular system")
    print("dE(int) is the vertical total interaction energy (without geometric relaxation)")
    print()
    print()
    print("Primary NOCV/ETS orbital interactions:")
    neg_vals,pos_vals,dE_ints = grab_NOCV_interactions("calcAB.out")

    print("-"*70)
    print(f"{'Neg. eigvals (e)':20}{'Pos. eigvals (e)':20}{'dE_orb (kcal/mol)':20}")
    print("-"*70)
    for n,p,e in zip(neg_vals,pos_vals,dE_ints):
        print(f"{n:>10.3f} {p:>20.3f} {e:>20.3f}")
    print("-"*70)
    print(f"Sum of orbital interactions: {sum(dE_ints):>23.3f} kcal/mol")


def grab_NOCV_interactions(file):
    grab=False
    neg_eigenvals=[]
    pos_eigenvals=[]
    DE_k=[]
    with open(file) as f:
        for line in f:
            if 'Consistency' in line:
                grab=False
            if grab is True:
                if len(line) >2:
                    neg_eigenvals.append(float(line.split()[0]))
                    pos_eigenvals.append(float(line.split()[1]))
                    DE_k.append(float(line.split()[-1]))
            if 'negative eigen. (e)' in line:
                grab=True

    return neg_eigenvals,pos_eigenvals,DE_k

#NOCV analysis using Multiwfn
#Need to figure out how to generalize more. 
#If Molden files is the best for Multiwfn then theory levels need to create those.
#TODO: Make internal theory methods for ORCATheory, xTBtheory, PySCF etc. ?? that outputs a Molden file ???
#NOTE: Benefit, multiwfn supports open-shell analysis
#NOTE: Proper ETS analysis by fockmatrix_approximation="ETS"
#NOTE: fockmatrix_approximation: regular gives approximate energies, same as Multiwfn
def NOCV_Multiwfn(fragment_AB=None, fragment_A=None, fragment_B=None, theory=None, gridlevel=2, openshell=False,
                            num_nocv_pairs=5, make_cube_files=True, numcores=1, fockmatrix_approximation="ETS"):
    print_line_with_mainheader("NOCV_Multiwfn")
    print("Will do full NOCV analysis with Multiwfn")
    print("gridlevel:", gridlevel)
    print("Numcores:", numcores)
    print()

    if fragment_AB.mult > 1 or fragment_A.mult > 1 or fragment_B.mult > 1:
        print("Multiplicity larger than 1. Setting openshell equal to True")
        openshell=True

    print("Openshell:", openshell)
    if isinstance(theory,ORCATheory) is not True:
        print("NOCV_Multiwfn currently only works with ORCATheory")
        ashexit()
    #A
    result_calcA=ash.Singlepoint(theory=theory, fragment=fragment_A)
    make_molden_file_ORCA(theory.filename+'.gbw') #TODO: Generalize
    os.rename("orca.molden.input", "A.molden.input")
    theory.cleanup()

    #B
    result_calcB=ash.Singlepoint(theory=theory, fragment=fragment_B)
    make_molden_file_ORCA(theory.filename+'.gbw')
    os.rename("orca.molden.input", "B.molden.input")
    theory.cleanup()

    #PromolAB
    original_orcablocks=theory.orcablocks #Keeping
    blockaddition="""
    %output
    Print[P_Iter_F] 1
    end
    %scf
    maxiter 1
    end
    """
    theory.orcablocks=theory.orcablocks+blockaddition
    theory.ignore_ORCA_error=True #Otherwise ORCA subprocess will fail due to maxiter=1 fail
    result_calcAB=ash.Singlepoint(theory=theory, fragment=fragment_AB)
    shutil.copy(f"{theory.filename}.out", "promol.out")
    #Get Fock matrix of promolstate I
    Fock_Pi_a, Fock_Pi_b = read_Fock_matrix_from_ORCA(f"{theory.filename}.out")
    np.savetxt("Fock_Pi_a",Fock_Pi_a)
    #exit()
    make_molden_file_ORCA(theory.filename+'.gbw')
    os.rename("orca.molden.input", "AB.molden.input")

    #AB
    theory.ignore_ORCA_error=False #Reverting
    theory.orcablocks=original_orcablocks+"%output Print[P_Iter_F] 1 end"
    result_calcAB=ash.Singlepoint(theory=theory, fragment=fragment_AB)
    #Get Fock matrix of Finalstate F
    Fock_Pf_a, Fock_Pf_b = read_Fock_matrix_from_ORCA(f"{theory.filename}.out")
    np.savetxt("Fock_Pf_a",Fock_Pf_a)
    make_molden_file_ORCA(theory.filename+'.gbw')
    os.rename("orca.molden.input", "AB.molden.input")

    #Extended transition state
    Fock_ETS_a = 0.5*(Fock_Pi_a + Fock_Pf_a)
    print("Fock_ETS_a:", Fock_ETS_a)
    if openshell is True:
        print("Fock_Pi_b:", Fock_Pi_b)
        if Fock_Pi_b is None:
            print("No beta Fock matrix found in ORCA output. Make sure UHF/UKS keywords were added")
            ashexit()
        print("Fock_Pi_b:", Fock_Pi_b)
        print("Fock_Pf_b:", Fock_Pf_b)
        Fock_ETS_b = 0.5*(Fock_Pi_b +Fock_Pf_b)
        print("Fock_ETS_b:", Fock_ETS_b)
    else:
        Fock_ETS_b=None

    #Write ETS Fock matrix in lower-triangular form for Multiwfn: F(1,1) F(2,1) F(2,2) F(3,1) F(3,2) F(3,3) ... F(nbasis,nbasis)
    if fockmatrix_approximation  == 'ETS':
        print("fockmatrix_approximation: ETS")
        fockfile="Fock_ETS"
        print("Fock_ETS_a:", Fock_ETS_a)
        print("Fock_ETS_b:", Fock_ETS_b)
        write_Fock_matrix_ORCA_format(fockfile, Fock_a=Fock_ETS_a,Fock_b=Fock_ETS_b, openshell=openshell)
    elif fockmatrix_approximation  == 'initial':
        print("fockmatrix_approximation: initial (unconverged AB Fock matrix)")
        fockfile="Fock_Pi"
        print("Fock_Pi_a:", Fock_Pi_a)
        print("Fock_Pi_b:", Fock_Pi_b)
        write_Fock_matrix_ORCA_format(fockfile, Fock_a=Fock_Pi_a,Fock_b=Fock_Pi_b, openshell=openshell)
    elif fockmatrix_approximation  == 'final':
        print("fockmatrix_approximation: final (converged AB Fock matrix)")
        fockfile="Fock_Pf"
        print("Fock_Pf_a:", Fock_Pf_a)
        print("Fock_Pf_b:", Fock_Pf_b)
        write_Fock_matrix_ORCA_format(fockfile, Fock_a=Fock_Pf_a,Fock_b=Fock_Pf_b, openshell=openshell)
    else:
        print("Unknown fockmatrix_approximation")
        ashexit()
    print("fockfile:", fockfile)
    #NOTE: Important Writing Fock matrix in ORCA format (with simple header) so that Multiwfn recognized it as such and used ORCA ordering of columns
    # Writing out as simple lower-triangular form does not work due to weird column swapping

    #Call Multiwfn
    multiwfn_run("AB.molden.input", option='nocv', grid=gridlevel, 
                    fragmentfiles=["A.molden.input","B.molden.input"],
                    fockfile=fockfile, numcores=numcores, openshell=openshell)

    #OTOD: openshell
    deltaE_int=(result_calcAB.energy - result_calcA.energy - result_calcB.energy)*hartokcal
    deltaE_orb=float(pygrep(" Sum of pair energies:","NOCV.txt")[-2])
    deltaE_steric=deltaE_int-deltaE_orb #Elstat+Pauli. Further ecomposition not possibly at the moment

    print()
    print("="*20)
    print("Basic EDA analysis")
    print("="*20)
    print()
    print("-"*50)
    print(f"{'dE(steric)':<20s} {deltaE_steric:>14.3f} kcal/mol")
    print(f"{'dE(orb)':<20s} {deltaE_orb:>14.3f} kcal/mol")
    print(f"{'dE(int)':<20s} {deltaE_int:>14.3f} kcal/mol")
    print("-"*50)
    print("E(steric) is sum of electrostatic and Pauli repulsion")
    print("dE(orb) is the NOCV-ETS orbital-relaxation of orthogonalized promolecular system")
    if fockmatrix_approximation == "initial" or fockmatrix_approximation == "final":
        print("Warning: Fock matrix approximation is initial or final")
        print("Warning: dE(orb) term is approximated when calculated by Multiwfn (as the correct TS Fock matrix is not used)")
    print("dE(int) is the vertical total interaction energy (without geometric relaxation)")

    #TODO: Grab orbital-interaction stuff from NOCV.txt and print here also
    print()
    print("TODO: NOCV orbital table to come here. See NOCV.txt for now")


def read_Fock_matrix_from_ORCA(file):
    grabA=False
    grabB=False
    foundbeta=False
    i_counter=0
    with open(file) as f:
        for line in f:
            if 'Number of basis functions                   ...' in line:
                ndim=int(line.split()[-1])
            if grabA is True:
                Acounter+=1                  
                if Acounter % (ndim+1) == 0:
                    col_indices=[int(i) for i in line.split()]
                if Acounter >= 1:
                    line_vals=[float(i) for i in line.split()[1:]]
                    for colindex,val in zip(col_indices,line_vals):
                        a=colindex
                        b=int(line.split()[0])
                        Fock_matrix_a[b,a] = val
                        i_counter+=1
                    if a == b == ndim-1:
                        grabA=False
            if grabB is True:
                Bcounter+=1                  
                if Bcounter % (ndim+1) == 0:
                    col_indices=[int(i) for i in line.split()]
                if Bcounter >= 1:
                    line_vals=[float(i) for i in line.split()[1:]]
                    for colindex,val in zip(col_indices,line_vals):
                        a=colindex
                        b=int(line.split()[0])
                        Fock_matrix_b[b,a] = val
                        i_counter+=1
                    if a == b == ndim-1:
                        grabB=False
            if 'Fock matrix for operator 0' in line:
                grabA=True
                Acounter=-1
                Fock_matrix_a=np.zeros((ndim,ndim))
            if 'Fock matrix for operator 1' in line:
                foundbeta=True
                grabB=True
                Bcounter=-1
                Fock_matrix_b=np.zeros((ndim,ndim))
    #Write
    np.savetxt("Fock_matrix_a",Fock_matrix_a)
    if foundbeta is True:
        print("Found beta Fock matrix")
        np.savetxt("Fock_matrix_b",Fock_matrix_b)
    else:
        Fock_matrix_b=None
    return Fock_matrix_a, Fock_matrix_b


def write_Fock_matrix_ORCA_format(outputfile, Fock_a=None,Fock_b=None, openshell=False):
    print("Fock_a:", Fock_a)
    print("Fock_b:", Fock_b)
    print("Writing Fock matrix alpha")
    with open(outputfile,'w') as f:
        f.write("                                 *****************\n")
        f.write("                                 * O   R   C   A *\n")
        f.write("                                 *****************\n")
        f.write(f"Fock matrix for operator 0\n")
        #f.write("\n")
        Fock_alpha = get_Fock_matrix_ORCA_format(Fock_a)
        f.write(Fock_alpha)
        #f.write("\n")
        if openshell is True:
            print("Writing Fock matrix beta")
            f.write(f"Fock matrix for operator 1\n")
            f.write("\n")
            Fock_beta = get_Fock_matrix_ORCA_format(Fock_b)
            f.write(Fock_beta)

#Get 
def get_Fock_matrix_ORCA_format(Fock):
    finalstring=""
    dim=Fock.shape[0]
    orcacoldim=6
    index=0
    tempvar=""
    chunks=dim//orcacoldim
    left=dim%orcacoldim
    xvar="                  "
    col_list=[]
    if left > 0:
        chunks=chunks+1
    for chunk in range(chunks):
        if chunk == chunks-1:
            if left == 0:
                left=6
            for temp in range(index,index+left):
                col_list.append(str(temp))
        else:
            for temp in range(index,index+orcacoldim):
                col_list.append(str(temp))
        col_list_string='          '.join(col_list)
        finalstring=finalstring+f"{xvar}{col_list_string}\n"
        col_list=[]
        for i in range(0,dim):

            if chunk == chunks-1:
                for k in range(index,index+left):
                    valstring=f"{Fock[i,k]:9.6f}"
                    tempvar=f"{tempvar}  {str(valstring)}"
            else:
                for k in range(index,index+orcacoldim):
                    valstring=f"{Fock[i,k]:9.6f}"
                    tempvar=f"{tempvar}  {str(valstring)}"
            finalstring=finalstring+f"{i:>7d}    {tempvar}\n"
            tempvar=""
        index+=6
    return finalstring