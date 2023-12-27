#plasma3DClass.py
#Description:   Enable perturbed or M3DC1 3D plasmas in HEAT
#Engineer:      A. Wingen
#Date:          20230227
import sys
import pandas as pd
import numpy as np
import scipy.interpolate as scinter
import scipy.integrate as integ
import os, glob
import shutil
import logging
import subprocess
import toolsClass
tools = toolsClass.tools()
log = logging.getLogger(__name__)

#==========================================================================================================================
#   plasma3D class
#==========================================================================================================================
class plasma3D:
	"""
	This class gets the unshadowed points, writes a points file, launches laminar_mpi,
	reads the output file and uses the penetration depth to calculate a 3D heat flux
	Example call:
	   plasma3D = plasma3DClass.plasma3D(inputFile = 'HEATinput.csv')
	   plasma3D.updatePoints(R, phi, Z)
	   plasma3D.launchLaminar(4, 'testrun')
	"""

	def __init__(self):
		self.R = None		# in m
		self.phi = None		# right-handed angle in degrees
		self.Z = None		# in m
		self.psimin = None
		self.Lc = None		# in km
		
		# Boundary Box limits
		self.bbRmin = None	
		self.bbRmax = None
		self.bbZmin = None
		self.bbZmax = None
		
		self.loadHF = False
		self.loadBasePath = None
		
		self.allowed_vars = ['plasma3Dmask','shot','time','tmax','gFile','itt','response',
				'selectField','useIcoil','sigma','charge','Ekin','Lambda','Mass','loadHF',
				'loadBasePath']
	
	
	def initializePlasma3D(self, shot, time, gFile = None, inputFile = None, cwd = None, inputDir = None):
		"""
		Set up basic input vars
		gfile should include the full path and file name
		inputFile is the main .csv file with input variables
		cwd is the HEAT data folder on the host machine for this shot and pfc, typically ~/HEAT/data/<machine>_<shot>_<tag>/<time>/<pfcName>
		inputDir is the folder in the docker container with input files, typically $HEAT_HOME/terminal/<machine>
		"""
		self.shot = tools.makeInt(shot)
		self.time = tools.makeInt(time)
		if cwd is None: self.cwd = os.getcwd()
		else: self.cwd = cwd
		if inputDir is None: self.inputDir = self.cwd
		else: self.inputDir = inputDir
		if gFile is None: gFile = self.cwd + '/g' + format(int(self.shot),'06d') + '.' + format(int(self.time),'05d')
		self.gFile = gFile
		if inputFile is not None: self.read_input_file(inputFile)
		else: self.setMAFOTctl()	# just defaults
		self.readM3DC1supFile()


	def setupNumberFormats(self, tsSigFigs=6, shotSigFigs=6):
		"""
		sets up pythonic string number formats for shot and timesteps
		"""
		self.tsFmt = "{:."+"{:d}".format(tsSigFigs)+"f}"
		self.shotFmt = "{:0"+"{:d}".format(shotSigFigs)+"d}"
		return
		
		
	def print_settings(self):
		"""
		Print all inputs
		"""
		print('#=============================================================')
		print('#                Equilibrium Variables')
		print('#=============================================================')
		print('shot = ' + str(self.shot))
		print('time = ' + str(self.time))
		print('gFile = ' + str(self.gFile))
		print('cwd = ' + str(self.cwd))
		print('#=============================================================')
		print('#                3D Plasma Variables')
		print('#=============================================================')
		print('plasma3Dmask = ' + str(self.plasma3Dmask))
		print('itt = ' + str(self.itt))
		print('useIcoil = ' + str(self.useIcoil))
		print('sigma = ' + str(self.sigma))
		print('charge = ' + str(self.charge))
		print('Ekin = ' + str(self.Ekin))
		print('Lambda = ' + str(self.Lambda))
		print('Mass = ' + str(self.Mass))
		print('#=============================================================')
		print('#                Boundary Box Variables')
		print('#=============================================================')
		print('Rmin = ' + str(self.bbRmin))
		print('Rmax = ' + str(self.bbRmax))
		print('Zmin = ' + str(self.bbZmin))
		print('Zmax = ' + str(self.bbZmax))
		print('#=============================================================')
		print('#                M3D-C1 Variables')
		print('#=============================================================')
		print('response = ' + str(self.response))
		print('selectField = ' + str(self.selectField))
		for i in range(len(self.C1Files)):
			print('File ' + str(i+1) + ' = ' + self.C1Files[i])
			print('   Scale = ' + str(self.C1scales[i]))
			print('   Phase = ' + str(self.C1phases[i]))
		

	def setMAFOTctl(self, itt = 300, response = 0, selectField = -1, useIcoil = 0, 
				sigma = 0, charge = -1, Ekin = 10, Lambda = 0.1, Mass = 2):
		"""
		Set the MAFOT specific class variables
		"""
		self.itt = tools.makeInt(itt) 						# toroidal iterations
		self.response = tools.makeInt(response) 			# M3D-C1 Plasma Response (0=no,>1=yes)
		self.selectField = tools.makeInt(selectField) 		# MHD fields to use (-3=VMEC,-2=SIESTA,-1=gfile,M3DC1:0=Eq,1=I-coil,2=both)
		self.useIcoil = tools.makeInt(useIcoil) 			# 0=no, 1=yes
		self.sigma = tools.makeInt(sigma) 					# Particle Direction (1=co-pass,-1=ctr-pass,0=field-lines)
		self.charge = tools.makeInt(charge) 				# Partile Charge (-1=electrons,>=1=ions)
		self.Ekin = tools.makeFloat(Ekin) 					# Particle Kinetic Energy in keV
		self.Lambda = tools.makeFloat(Lambda) 				# Ratio of perpendicular to parallel velocity
		self.Mass = tools.makeInt(Mass) 					# Particle Ion Mass (H=1, D=2, He=4)


	def setBoundaryBox(self, MHD, CAD):
		self.bbRmin = np.min([CAD.Rmin, MHD.ep[0].g['wall'][:,0].min()])
		self.bbRmax = np.max([CAD.Rmax, MHD.ep[0].g['wall'][:,0].max()])
		self.bbZmin = np.min([CAD.Zmin, MHD.ep[0].g['wall'][:,1].min()])
		self.bbZmax = np.max([CAD.Zmax, MHD.ep[0].g['wall'][:,1].max()])


	def setM3DC1input(self, C1Files = ['./C1.h5'], scales = [1], phases = None):
		"""
		Set the M3D-C1 specific class variables
		"""
		self.C1Files = C1Files 
		self.C1scales = scales
		if phases is None: self.C1phases = np.zeros(len(C1Files))
		else: self.C1phases = phases
	
	
	def readM3DC1supFile(self):
		"""
		Read M3D-C1 supplemental input file, if it already exists
		"""
		C1Files = []
		scales = []
		phases = []

		if not os.path.isfile(self.inputDir + '/' + 'm3dc1sup.in'): 
			print('m3dc1sup.in file not found!')
			log.info('m3dc1sup.in file not found!')
			self.setM3DC1input()
			return
		
		with open(self.inputDir + '/' + 'm3dc1sup.in') as f:
			lines = f.readlines()
		
		for line in lines:
			line = line.strip()
			if len(line) < 1: continue
			if line[0] == '#': continue
			words = line.split()
			c1file = words[0]
			if ('./' in c1file): c1file = c1file.replace('./', self.inputDir + '/')
			C1Files.append(c1file)
			scales.append(tools.makeFloat(words[1]))
			if len(words) > 2: phases.append(tools.makeFloat(words[2]))
			else: phases.append(0)
		
		if len(C1Files) < 1: 
			print('Error reading m3dc1sup.in')
			log.info('Error reading m3dc1sup.in')
			self.setM3DC1input()
			return
		else:
			self.setM3DC1input(C1Files, scales, phases)
			print('M3D-C1: ' + self.inputDir + '/' + 'm3dc1sup.in read successfully')
			log.info('M3D-C1: ' + self.inputDir + '/' + 'm3dc1sup.in read successfully')
			return
		
	
	def read_input_file(self, file):
		"""
		Reads the 3D plasma csv input file
		Format for input file is comma delimited, # are comments.
		Example:
		#Important Comment
		variable_name, value
		"""
		if os.path.isfile(file): 
			tools.read_input_file(self, file)
			self.setTypes()
			print('Input file: ' + file + ' read successfully')
			log.info('Input file: ' + file + ' read successfully')
		else: 
			print('Input file: ' + file + ' not found!')
			log.info('Input file: ' + file + ' not found!')
			self.setMAFOTctl()	# just defaults


	def setTypes(self):
		"""
		Set variable types for the stuff that isnt a string from the input file
		"""
		integers = ['plasma3Dmask','shot','time','tmax','itt','response','selectField','useIcoil','sigma','charge','Mass']
		floats = ['Ekin','Lambda']
		bools = ['loadHF']
		setAllTypes(self, integers, floats, bools)     # this is not a typo, but the correct syntax for this call


	def updatePointsFromCenters(self, xyz):
		"""
		Converts xyz of centers into R,phi,Z, update class variables and write the points file
		"""
		if len(xyz.shape) > 1:
			R,Z,phi = tools.xyz2cyl(xyz[:,0],xyz[:,1],xyz[:,2])
		else:
			R,Z,phi = tools.xyz2cyl(xyz[0],xyz[1],xyz[2])

		phi = np.degrees(phi)
		self.updatePoints(R, phi, Z)
		
	
	def updatePoints(self, R, phi, Z):
		"""
		Get arrays of R,phi,Z, update class variables and write the points file
		"""
		self.R = R
		self.phi = phi 
		self.Z = Z
		self.writePoints()

	
	def writePoints(self, filename = 'points3DHF.dat'):
		"""
		Write the points file in CWD from the class variables
		"""
		R = self.R.flatten()
		phi = self.phi.flatten()
		Z = self.Z.flatten()
		N = len(R)
		
		with open(self.cwd + '/' + filename,'w') as f:
			f.write("# Number of points = " + str(N) + "\n")
			for i in range(N): 
				f.write(str(R[i]) + "\t" + str(phi[i]) + "\t" + str(Z[i]) + "\n")
				
				
	def launchLaminar(self, nproc, tag = None, MapDirection = 0):
		"""
		Write all input files and launch MAFOT
		Read the output file when finished
		"""
		if tag is None: tag = ''
		self.writeControlFile(MapDirection)
		self.writeM3DC1supFile()
		self.writeCoilsupFile()
		
		if nproc > 20: nproc = 20
		self.nproc = nproc
		self.tag = tag
		print('Launching 3D plasma field line tracing')
		log.info('Launching 3D plasma field line tracing')
		
		bbLimits = str(self.bbRmin) + ',' + str(self.bbRmax) + ',' + str(self.bbZmin) + ',' + str(self.bbZmax)
		args = ['mpirun','-n',str(nproc),'heatlaminar_mpi','-P','points3DHF.dat','-B',bbLimits,'_lamCTL.dat',tag]
		current_env = os.environ.copy()        #Copy the current environment (important when in appImage mode)
		subprocess.run(args, env=current_env, cwd=self.cwd)
		#print('mpirun -n ' + str(nproc) + ' heatlaminar_mpi' + ' -P points3DHF.dat' + ' _lamCTL.dat' + ' ' + tag)
		
		#self.wait2finish(nproc, tag)
		self.readLaminar(tag)
		print('3D plasma field line tracing complete')
		log.info('3D plasma field line tracing complete')
		
	
	def readLaminar(self, tag = None, path = None):
		"""
		Read the MAFOT outputfile and set psimin and Lc class variables
		"""
		if tag is None: tag = ''    # this tag has len(tag) = 0
		if path is None: path = self.cwd
		
		file = path + '/' + 'lam_' + tag + '.dat'
		if os.path.isfile(file): 
			lamdata = np.genfromtxt(file,comments='#')
			self.Lc = lamdata[:,3]
			self.psimin = lamdata[:,4]
		else:
			print('MAFOT output file: ' + file + ' not found!')
			log.info('MAFOT output file: ' + file + ' not found!')
		return


	def copyAndRead(self, path, tag = None):
		"""
		Copy all input files and MAFOT laminar data from path into self.cwd
		Read the output file when finished
		"""
		if tag is None: tag = ''    # this tag has len(tag) = 0
		
		#self.writeControlFile(MapDirection)
		src = path + '/' + '_lamCTL.dat'
		dst = self.cwd + '/' + '_lamCTL.dat'
		if os.path.isfile(src): 
			shutil.copy(src, dst)

		#self.writeM3DC1supFile()
		src = path + '/' + 'm3dc1sup.in'
		dst = self.cwd + '/' + 'm3dc1sup.in'
		if os.path.isfile(src): 
			shutil.copy(src, dst)

		#self.writeCoilsupFile()
		
		src = path + '/' + 'lam_' + tag + '.dat'
		dst = self.cwd + '/' + 'lam_' + tag + '.dat'
		if os.path.isfile(src): 
			shutil.copy(src, dst)
		else:
			print('MAFOT output file: ' + src + ' not found!')
			log.info('MAFOT output file: ' + src + ' not found!')
		
		print('Copy and load 3D MAFOT Laminar data from file: ' + src)
		log.info('Copy and load 3D MAFOT Laminar data from file: ' + src)
		self.readLaminar(tag)
		return
		
		
	def checkValidOutput(self):
		""" 
		Check for invalid points in the laminar run: psimin == 10
		"""
		idx = np.where(self.psimin == 10)[0]
		invalid = np.zeros(len(self.psimin), dtype=bool)
		invalid[idx] = True
		print('Number of points for which Laminar run could not compute psimin:', np.sum(invalid))
		log.info('Number of points for which Laminar run could not compute psimin: ' + str(np.sum(invalid)))
		return invalid
			
			
	def cleanUp(self, tag = None):
		logs = self.cwd + '/' + 'log*'
		for f in glob.glob(logs): os.remove(f)


	def writeControlFile(self, MapDirection):
		"""
		Write MAFOT control file
		"""
		with open(self.cwd + '/' + '_lamCTL.dat', 'w') as f:
			f.write('# Parameterfile for HEAT Programs\n')
			f.write('# Shot: ' + format(int(self.shot),'06d') + '\tTime: ' + format(int(self.time),'04d') + 'ms\n')
			f.write('# Path: ' + self.gFile + '\n')
			f.write('NZ=\t10\n')
			f.write('itt=\t' + str(self.itt) + '\n')
			f.write('Rmin=\t1\n')
			f.write('Rmax=\t2\n')
			f.write('Zmin=\t-1\n')
			f.write('Zmax=\t1\n')
			f.write('NR=\t10\n')
			f.write('phistart(deg)=\t0\n')
			f.write('MapDirection=\t' + str(MapDirection) + '\n')
			f.write('PlasmaResponse(0=no,>1=yes)=\t' + str(self.response) + '\n')
			f.write('Field(-3=VMEC,-2=SIESTA,-1=gfile,M3DC1:0=Eq,1=I-coil,2=both)=\t' + str(self.selectField) + '\n')
			f.write('target(0=cp,1=inner,2=outer,3=shelf)=\t0\n')
			f.write('createPoints(0=setR,3=setpsi)=\t0\n')    # This must be entry index 12
			f.write('unused=\t0\n')
			f.write('unused=\t0\n')
			f.write('unused=\t0\n')
			f.write('ParticleDirection(1=co-pass,-1=ctr-pass,0=field-lines)=\t' + str(self.sigma) + '\n')   # This must be entry index 16
			f.write('PartileCharge(-1=electrons,>=1=ions)=\t' + str(self.charge) + '\n')
			f.write('Ekin[keV]=\t' + str(self.Ekin) + '\n')
			f.write('lambda=\t' + str(self.Lambda) + '\n')
			f.write('Mass=\t' + str(self.Mass) + '\n')    # This must be entry index 20
			f.write('unused=\t0\n')
			f.write('unused=\t0\n')
			f.write('dpinit=\t1.0\n')   # This must be entry index 23
			f.write('pi=\t3.141592653589793\n')
			f.write('2*pi=\t6.283185307179586\n')


	def writeM3DC1supFile(self):
		"""
		Write M3D-C1 supplemental input file
		Overwrites any existing one.
		"""
		with open(self.cwd + '/' + 'm3dc1sup.in', 'w') as f:
			for i in range(len(self.C1Files)):
				f.write(self.C1Files[i] + '\t' + str(self.C1scales[i]) + '\t' + str(self.C1phases[i]) + '\n')


	def writeCoilsupFile(self, machine = None):
		"""
		This would be machine specific and needs updating in the future
		"""
		# with open(self.cwd + '/' + 'heatsup.in', 'w') as f:
		#	pass

		return
		
	
	def wait2finish(self, nproc, tag):
		import time
		print ('Waiting for job to finish...', end='')
		time.sleep(5)	# wait 5 seconds
		while(self.isProcessRunning()):
			time.sleep(60)		# wait 1 minute; no CPU usage
		print('done')
			
		if not self.isComplete():
			print('MAFOT run ended prematurely. Attempt restart...')
			subprocess.call(['mpirun','-n',str(nproc),'heatlaminar_mpi','-P','points.dat','_lamCTL.dat',tag])
			self.wait2finish(nproc, tag)
		else: return
	
	
	def isComplete(self, logsPath = None):
		if logsPath is None: 
			logsPath = self.cwd
		if not logsPath[-1] == '/': logsPath += '/'

		allFiles = os.listdir(logsPath)
		fileList = [f for f in allFiles if '_Master.dat' in f]
		for file in fileList:
			lines = subprocess.check_output(['tail', file])
			lines = lines.decode('UTF-8').split('\n')
			for line in lines: 
				if 'Program terminates normally' in line: 
					return True
			else: 
				return False
	
	
	def isProcessRunning(self):
		import getpass
		user = getpass.getuser()
		lines = subprocess.check_output('ps aux | grep heatlaminar_mpi', shell = True)	# for some reason only works with shell = True
		lines = lines.decode('UTF-8').split('\n')
		for line in lines:
			if ('mpirun' in line) & (user in line):
				self.pid = int(line.strip().split()[1])
				return True
		self.pid = -1
		return False
		

#==========================================================================================================================
#   heatflux3D class
#==========================================================================================================================
class heatflux3D:
	"""
	This class gets the penetration depth and connection length of unshadowed points
	and calculates the parallel heat flux
	still needs normalization to power balance
	still needs incident angle
	Example call:
	"""

	def __init__(self):
		self.psimin = None
		self.Lc = None		# in km
		self.N = 1
		self.q = np.zeros(self.N)
		self.ep = None	# equilParams_class instance for EFIT equilibrium
		self.HFS = None	# True: use high field side SOL, False: use low field side SOL
		self.teProfileData = None
		self.neProfileData = None
		self.allowed_vars = ['Lcmin', 'lcfs', 'lqCN', 'S', 'P', 'coreRadFrac', 'qBG', 
				'teProfileData', 'neProfileData', 'kappa', 'model']


	def initializeHF3D(self, ep, inputFile = None, cwd = None, inputDir = None):
		"""
		Set up basic input vars
		"""
		#self.N = len(self.Lc)
		#self.q = np.zeros(self.N)
		self.ep = ep	# equilParams_class instance for EFIT equilibrium
		
		if inputDir is None: self.inputDir = os.getcwd()
		else: self.inputDir = inputDir
		if cwd is None: self.cwd = os.getcwd()
		else: self.cwd = cwd
		if inputFile is not None: self.read_input_file(inputFile)
		else: self.setHFctl()	# just defaults	
		self.Psol = (1 - self.coreRadFrac) * self.P
			
		T = self.teProfileData
		ne = self.neProfileData

		# set T profile
		if T is None: T = 2							# temperature at top of pedestal in keV
		if isinstance(T, str):						# file name for T profile data
			if ('./' in T) | ('/' not in T): path = self.inputDir + '/'
			else: path = ''
			if not os.path.isfile(path + T): 
				raise RuntimeError(path + T + ' file not found!')
			print('Loading T profile data from: ' + path + T)
			log.info('Loading T profile data from: ' + path + T)
			TData = np.loadtxt(path + T)
			psiT = TData[:,0]
			T = TData[:,1]
			self.fT = scinter.UnivariateSpline(psiT, T, s = 0, ext = 'const')
		elif isinstance(T, np.ndarray):				# array of T data assuming psi = [0, 1.1]
			psiT = np.linspace(0, 1.1, len(T))
			self.fT = scinter.UnivariateSpline(psiT, T, s = 0, ext = 'const')
		else:										# any other option
			try:
				T = float(T)						# temperature at top of pedestal in keV
				self.fT = lambda x: Tprofile(x, T)	# generic temperature profile
			except:
				raise RuntimeError('Invalid T profile data')

		# set density profile
		if ne is None: ne = 0.5							# electron density at top of pedestal in 1e-20/m^3
		if isinstance(ne, str):							# file name for density profile data
			if ('./' in ne) | ('/' not in ne): path = self.inputDir + '/'
			else: path = ''
			if not os.path.isfile(path + ne): 
				raise RuntimeError(path + ne + ' file not found!')
			print('Loading ne profile data from: ' + path + ne)
			log.info('Loading ne profile data from: ' + path + ne)
			neData = np.loadtxt(path + ne)
			nePsi = neData[:,0]
			ne = neData[:,1]
			self.fn = scinter.UnivariateSpline(nePsi, ne, s = 0, ext = 'const')
		elif isinstance(ne, np.ndarray):				# array of density data assuming psi = [0, 1.1]
			nePsi = np.linspace(0, 1.1, len(ne))
			self.fn = scinter.UnivariateSpline(nePsi, ne, s = 0, ext = 'const')
		else:											# any other option
			try:
				ne = float(ne)							# density at top of pedestal in 1e-20/m^3
				self.fn = lambda x: ne + x*0					# generic density profile
			except:
				raise RuntimeError('Invalid density profile data')


	def setupNumberFormats(self, tsSigFigs=6, shotSigFigs=6):
		"""
		sets up pythonic string number formats for shot and timesteps
		"""
		self.tsFmt = "{:."+"{:d}".format(tsSigFigs)+"f}"
		self.shotFmt = "{:0"+"{:d}".format(shotSigFigs)+"d}"
		return

	
	def print_settings(self):
		"""
		Print all inputs
		"""
		print('#=============================================================')
		print('#                Optical HF Variables')
		print('#=============================================================')
		print('lqCN = ' + str(self.lqCN))
		print('S = ' + str(self.S))
		print('P = ' + str(self.P))
		print('coreRadFrac = ' + str(self.coreRadFrac))
		print('qBG = ' + str(self.qBG))
		print('kappa = ' + str(self.kappa))
		print('#=============================================================')
		print('#                3D Plasma Variables')
		print('#=============================================================')
		print('Lcmin = ' + str(self.Lcmin))
		print('lcfs = ' + str(self.lcfs))
		print('teProfileData = ' + str(self.teProfileData))
		print('neProfileData = ' + str(self.neProfileData))
		print('model = ' + str(self.model))
		

	def setHFctl(self, Lcmin = 0.075, lcfs = 0.97, lqCN = 5, S = 2, P = 10, coreRadFrac = 0.0, qBG = 0, kappa = 2000):
		"""
		Set the specific class variables
		"""
		self.Lcmin = tools.makeFloat(Lcmin) 		# minimum connection length in SOL to separateout the PFR, in km
		self.lcfs = tools.makeFloat(lcfs) 			# psi of the Last Closed Flux Surface inside the stochastic layer
		self.lqCN = tools.makeFloat(lqCN) 		    # heat flux layer width for Eich profile, in mm
		self.S = tools.makeFloat(S) 				# heat flux layer extension width in PFR, in mm
		self.P = tools.makeFloat(P) 				# total power into SOL, in MW
		self.coreRadFrac = tools.makeFloat(coreRadFrac)  # fraction of radiated power
		self.qBG = tools.makeFloat(qBG) 			# background heat flux in MW/m^2
		self.kappa = tools.makeFloat(kappa) 		# electron heat conductivity in W/m/eV^3.5
		self.teProfileData = None
		self.neProfileData = None
		self.model = None
	
	
	def read_input_file(self, file):
		"""
		Reads the 3D plasma csv input file
		Format for input file is comma delimited, # are comments.
		Example:
		#Important Comment
		variable_name, value
		"""
		if os.path.isfile(file): 
			tools.read_input_file(self, file)
			self.setTypes()
			print('Input file: ' + file + ' read successfully')
			log.info('Input file: ' + file + ' read successfully')
		else: 
			print('Input file: ' + file + ' not found!')
			log.info('Input file: ' + file + ' not found!')
			self.setHFctl()	# just defaults
		

	def setTypes(self):
		"""
		Set variable types for the stuff that isnt a string from the input file
		"""
		integers = []
		floats = ['Lcmin', 'lcfs', 'lqCN', 'S', 'P', 'coreRadFrac', 'qBG', 'kappa']
		bools = []
		setAllTypes(self, integers, floats, bools)
		
		# data is an array or list
		if self.teProfileData is not None:
			if '[' in self.teProfileData:
				from ast import literal_eval
				self.teProfileData = self.teProfileData.replace(' ',',')
				self.teProfileData = np.array(literal_eval(self.teProfileData))
		if self.neProfileData is not None:
			if '[' in self.neProfileData:
				from ast import literal_eval
				self.neProfileData = self.neProfileData.replace(' ',',')
				self.neProfileData = np.array(literal_eval(self.neProfileData))
		
		# check if data is just a float
		try: self.teProfileData = float(self.teProfileData)
		except: pass	# data is a file name and remains a string or None
		try: self.neProfileData = float(self.neProfileData)
		except: pass	# data is a file name and remains a string or None
			
		
	def updateLaminarData(self, psimin, Lc):
		"""
		updates member variables for psimin, connection length, 
		checks for invalid points and finds private flux region points
		"""
		self.psimin = psimin
		self.Lc = Lc
		self.N = len(self.psimin)
		self.q = np.zeros(self.N)
		self.good = self.isGoodPoint()
		self.pfr = self.isPFR()
		
		
	def isGoodPoint(self):
		"""
		Returns a boolean mask for points to use or not
		True: good point
		False: point failed during laminar run
		"""
		mask = np.ones(self.N, dtype = bool)
		fails = np.where(self.psimin == 10)
		mask[fails] = False
		return mask
		
		
	def isPFR(self):
		"""
		Returns a boolean mask for point in PFR or not
		True: point in PFR
		False: point in SOL or lobes
		"""
		mask = np.zeros(self.N, dtype = bool)
		pfr = np.where((self.psimin < 1) & (self.Lc < self.Lcmin))
		mask[pfr] = True
		return mask
		
	
	def heatflux(self, DivCode, powerFrac):
		"""
		computes self.q for the chosen model
		zeroes out invalid points
		updates self.q
		"""
		print('3D Heat flux model type: ' + self.model)
		log.info('3D Heat flux model type: ' + self.model)
		if self.model in ['Layer', 'layer', 'eich', 'Eich', 'heuristic']:
			if 'O' in DivCode: HFS = False		# an Outer divertor is on low-field-side
			elif 'I' in DivCode: HFS = True		# an Inner divertor is on High-Field-Side
			else: raise ValueError('PFC Divertor Code cannot be identified. Check your PFC input file')
			self.HFS = HFS	# True: use high field side SOL, False: use low field side SOL
			print('Layer width lq =', self.lqCN)
			print('PFR spread S =', self.S)
			print('LCFS at', self.lcfs)
			print('Is on HFS:', self.HFS)
			log.info('Layer width lq = ' + str(self.lqCN))
			log.info('PFR spread S = ' + str(self.S))
			log.info('LCFS at ' + str(self.lcfs))
			log.info('Is on HFS: ' + str(self.HFS))
			q = self.getq_layer()	# normalized to qmax = 1
			q0 = self.scale_layer(self.lqCN, self.S, self.Psol*powerFrac)
		elif self.model in ['conduct', 'conductive']:
			L = np.mean(self.Lc[self.psimin > self.lcfs])*1e3	# average connection length in open field line area in m
			ratio = self.lqCN/self.S
			print('Conduction length L =', format(L,'.3f'), 'm')
			print('Ratio of SOL/PFR spread:', format(ratio,'.1f'))
			print('LCFS at', self.lcfs)
			log.info('Conduction length L = ' + format(L,'.3f') + 'm')
			log.info('Ratio of SOL/PFR spread: ' + format(ratio,'.1f'))
			log.info('LCFS at ' + str(self.lcfs))
			q = self.getq_conduct(self.psimin, kappa = self.kappa, L = L, pfr = self.pfr, ratio = ratio)
			q0 = self.scale_conduct(self.Psol*powerFrac, self.kappa, L, ratio)
		else:
			raise ValueError('No valid model selected')
		
		q *= q0
		print('Scaling Factor q0 =', q0)	
		print('Background qBG =', self.qBG)
		log.info('Scaling Factor q0 = ' + str(q0))
		log.info('Background qBG = ' + str(self.qBG))
		self.q[self.good] = q[self.good]
		self.q += self.qBG


	def getq_conduct(self, psi, kappa = 2000, T0 = 0, L = 1, limit = True, pfr = None, ratio = 3):
		"""
		Input:
		  kappa = electron heat conductivity in W/m/eV^3.5
		  T0 = electron temperature at sheath entrance near target in keV
		  L = conduction distance between target and LCFS in m
		  scale: estimate of SOL/PFR spreading, like lq/S for Eich profiles
		Output:
		  updates self.q		
		"""
		T = self.fT(psi)			# this is now temperature in keV
		
		if limit: 
			T[psi < self.lcfs] = self.fT(self.lcfs)
		if pfr is not None: T[pfr] = self.fT(1 + ratio*(1-psi[pfr]))	# treat T in PFR as if in SOL: map psi<1 to psi>1 with ratio * dpsi
		
		q = 2.0/7.0 * kappa/L * (T**3.5 - T0**3.5) * (1e+3)**3.5/1e+6   # in MW/m^2
		return q
		
		
	def scale_conduct(self, P, kappa, L, ratio, T0 = 0):
		"""
		Get scale factor q||0 (q0) for heat flux via power balance:
		(input MW = output MW)
		Ignores wall psi and just creates a profile at OMP
		Creates a dense (1000pts) grid at the midplane to get higher resolution
		integral.  Integrates q_hat with respect to psi.
		q||0 = P_div / ( 2*pi* integral(q_hat dPsi ))
		return q0		
		"""
		psiN = np.linspace(0.85, 1.2, 1000)	# this is normalized
		T = self.fT(psiN)			# this is now temperature in keV
		
		pfr = psiN < 1.0
		T[pfr] = self.fT(1.0 + ratio*(1.0-psiN[pfr]))	# treat T in PFR as if in SOL: map psi<1 to psi>1 with ratio * dpsi
		
		q_hat = 2.0/7.0 * kappa/L * (T**3.5 - T0**3.5) * (1e+3)**3.5/1e+6   # in MW/m^2
		
		psi = psiN * (self.ep.g['psiSep']-self.ep.g['psiAxis']) + self.ep.g['psiAxis']	# this is flux
		P0 = 2*np.pi * integ.simps(q_hat, psi)
		#account for nonphysical power
		if P0 < 0: P0 = -P0
		#Scale to input power
		q0 = P/P0
		return q0 #,q_hat,psiN


	def scale_conduct2(self, P, kappa, L, lq, S, ratio, T0 = 0, pfr = 1.0):
		"""
		"""		
		if pfr is None: pfr = self.lcfs
		runLaminar = True
		# Get a psi range that fully covers the profile for integration. Peak location does not matter, so use s0 from psi = 1.0
		Rlcfs = self.map_R_psi(self.lcfs)
		if self.HFS:
			Rmin = Rlcfs - 20.0*lq*(1e-3)		#in m
			if Rmin < min(self.ep.g['R']): Rmin = min(self.ep.g['R'])	#if Rmin outside EFIT grid, cap at minimum R of grid
			Rmax = Rlcfs + 20.0*S*(1e-3)		#in m
			if Rmax > self.ep.g['RmAxis']: Rmax = self.ep.g['RmAxis']	#if Rmax is outside the magnetic axis, psi would increase again, so cap at axis
		else:
			Rmin = Rlcfs - 20.0*S*(1e-3)		#in m
			if Rmin < self.ep.g['RmAxis']: Rmin = self.ep.g['RmAxis']	#if Rmin is inside the magnetic axis, psi would increase again, so cap at axis
			Rmax = Rlcfs + 20.0*lq*(1e-3)		#in m
			if Rmax > max(self.ep.g['R']): Rmax = max(self.ep.g['R'])	#if Rmax is outside EFIT grid, cap at maximum R of grid

		R = np.linspace(Rmin,Rmax,1000)
		Z = self.ep.g['ZmAxis']*np.ones(R.shape)
		
		# get q_hat from laminar		
		if self.HFS: tag = 'hfs_mp'
		else: tag = 'lfs_mp'
		file = self.inputDir + '/' + 'lam_' + tag + '.dat'
		if os.path.isfile(file): runLaminar = False

		if runLaminar:
			with open(self.inputDir + '/' + 'points_' + tag + '.dat','w') as f:
				for i in range(len(R)):
					f.write(str(R[i]) + '\t' + str(0.0) + '\t' + str(Z[i]) + '\n')
					
			nproc = 10
			args = ['mpirun','-n',str(nproc),'heatlaminar_mpi','-P','points_' + tag + '.dat','_lamCTL.dat',tag]
			current_env = os.environ.copy()        #Copy the current environment (important when in appImage mode)
			subprocess.run(args, env=current_env, cwd=self.inputDir)
			for f in glob.glob(self.inputDir + '/' + 'log*'): os.remove(f)		#cleanup
		
		if os.path.isfile(file): 
			lamdata = np.genfromtxt(file,comments='#')
			psimin = lamdata[:,4]
		else:
			print('File', file, 'not found') 
			log.info('File ' + file + ' not found') 

		idx = np.abs(psimin - pfr).argmin()
		mask = np.zeros(len(R), dtype = bool)
		if self.HFS: pfr = np.where(R > R[idx])[0]
		else: pfr = np.where(R < R[idx])[0]
		mask[pfr] = True
		
		T = self.fT(psimin)			# this is now temperature in keV		
		T[psimin < self.lcfs] = self.fT(self.lcfs)
		T[mask] = self.fT(1.0 + ratio*(1.0-psimin[mask]))	# treat T in PFR as if in SOL: map psi<1 to psi>1 with ratio * dpsi
		
		q_hat = 2.0/7.0 * kappa/L * (T**3.5 - T0**3.5) * (1e+3)**3.5/1e+6   # in MW/m^2
		
		#Menard's method
		psiN = self.ep.psiFunc.ev(R,Z)	# this is normalized
		psi = psiN * (self.ep.g['psiSep']-self.ep.g['psiAxis']) + self.ep.g['psiAxis']	# this is flux
		P0 = 2*np.pi * integ.simps(q_hat, psi)
		#account for nonphysical power
		if P0 < 0: P0 = -P0
		#Scale to input power
		q0 = P/P0
		return q0 #,q_hat,R,psiN,psi


	def getq_layer(self):
		"""
		Computes heat flux based on the flux layer profile with lobes
		updates self.q		
		"""
		q, q0 = self.set_layer(self.psimin, self.lqCN, self.S, lcfs = self.lcfs, lobes = True)
		if np.sum(self.pfr > 0): q[self.pfr],_ = self.set_layer(self.psimin[self.pfr], self.lqCN, self.S, q0 = q0)
		return q

	
	def set_layer(self, psi, lq, S, lcfs = 1.0, q0 = 1, lobes = False):
		"""
		psi is flat array of normalized flux
		lq is heat flux width at midplane in mm
		S is the private flux region spreading in mm
		returns flat array of heat flux based on Eich profile
		"""
		x = self.map_R_psi(psi)
		xsep = self.map_R_psi(1.0)

		# this only needs to resolve the peak well, no need to cover the entire profile, in case lq and S are large
		if lobes:
			s0 = self.map_R_psi(lcfs)
			s = self.map_R_psi(np.linspace(lcfs-0.05,lcfs+0.1,10000))
		else:
			s0 = self.map_R_psi(1.0)
			s = self.map_R_psi(np.linspace(0.95,1.1,10000))
			
		qref = eich_profile(s, lq, S, s0, q0 = 1, qBG = 0, fx = 1)
		idx = qref.argmax()
		smax = s[idx]
		qmax = qref[idx]
	
		if self.HFS:
			x *= -1
			s0 *= -1
			smax *= -1
			xsep *= -1
			x0 = smax
		else:
			x0 = s0 - (smax-s0)	# now the peak amplitude is at psi = lcfs; qlcfs = qmax too
					
		q = eich_profile(x, lq, S, x0, q0 = 1, qBG = 0, fx = 1)
		qsep = eich_profile(xsep, lq, S, x0, q0 = 1, qBG = 0, fx = 1)
		
		if lobes:
			q[psi < lcfs] = qmax
		
		return q/q.max()*q0, qsep/q.max()*q0


	def map_R_psi(self, psi, HFS = None):
		"""
		Map normalized poloidal flux psi to R at midplane (Z = 0)
		psi is flat array
		return R(psi)
		"""
		if HFS is None: HFS = self.HFS
		if HFS:
			R = np.linspace(self.ep.g['RmAxis'], self.ep.g['R1'], 100)
		else:
			R = np.linspace(self.ep.g['RmAxis'], self.ep.g['R1'] + self.ep.g['Xdim'], 100)
			
		Z = self.ep.g['ZmAxis']*np.ones(len(R))
		p = self.ep.psiFunc.ev(R,Z)
		
		f = scinter.UnivariateSpline(p, R, s = 0, ext = 'const')	# psi outside of spline domain return the boundary value
		return f(psi)
	
	
	def scale_layer(self, lq, S, P, pfr = 1.0):
		"""
		scales HF using a R-profile along the midplane at phi = 0
		q-profile is obtained using laminar and apply the heat flux layer to psimin
		Get scale factor q||0 (q0) for heat flux via power balance:
		(input MW = output MW)
		Creates a dense (1000pts) R-grid at the midplane (Z = Zaxis) to get higher resolution
		integral.  Integrates q_hat with respect to psi.
		q||0 = P_div / ( 2*pi* integral(q_hat dPsi ))
		return q0
		"""		
		if pfr is None: pfr = self.lcfs
		runLaminar = True
		# Get a psi range that fully covers the profile for integration. Peak location does not matter, so use s0 from psi = 1.0
		Rlcfs = self.map_R_psi(self.lcfs)
		if self.HFS:
			Rmin = Rlcfs - 20.0*lq*(1e-3)		#in m
			if Rmin < min(self.ep.g['R']): Rmin = min(self.ep.g['R'])	#if Rmin outside EFIT grid, cap at minimum R of grid
			Rmax = Rlcfs + 20.0*S*(1e-3)		#in m
			if Rmax > self.ep.g['RmAxis']: Rmax = self.ep.g['RmAxis']	#if Rmax is outside the magnetic axis, psi would increase again, so cap at axis
		else:
			Rmin = Rlcfs - 20.0*S*(1e-3)		#in m
			if Rmin < self.ep.g['RmAxis']: Rmin = self.ep.g['RmAxis']	#if Rmin is inside the magnetic axis, psi would increase again, so cap at axis
			Rmax = Rlcfs + 20.0*lq*(1e-3)		#in m
			if Rmax > max(self.ep.g['R']): Rmax = max(self.ep.g['R'])	#if Rmax is outside EFIT grid, cap at maximum R of grid

		R = np.linspace(Rmin,Rmax,1000)
		Z = self.ep.g['ZmAxis']*np.ones(R.shape)
		
		# get q_hat from laminar		
		if self.HFS: tag = 'hfs_mp'
		else: tag = 'lfs_mp'
		file = self.inputDir + '/' + 'lam_' + tag + '.dat'
		if os.path.isfile(file): runLaminar = False

		if runLaminar:
			with open(self.inputDir + '/' + 'points_' + tag + '.dat','w') as f:
				for i in range(len(R)):
					f.write(str(R[i]) + '\t' + str(0.0) + '\t' + str(Z[i]) + '\n')
					
			nproc = 10
			args = ['mpirun','-n',str(nproc),'heatlaminar_mpi','-P','points_' + tag + '.dat','_lamCTL.dat',tag]
			current_env = os.environ.copy()        #Copy the current environment (important when in appImage mode)
			subprocess.run(args, env=current_env, cwd=self.inputDir)
			for f in glob.glob(self.inputDir + '/' + 'log*'): os.remove(f)		#cleanup
		
		if os.path.isfile(file): 
			lamdata = np.genfromtxt(file,comments='#')
			psimin = lamdata[:,4]
		else:
			print('File', file, 'not found') 
			log.info('File ' + file + ' not found') 

		idx = np.abs(psimin - pfr).argmin()
		mask = np.zeros(len(R), dtype = bool)
		if self.HFS: pfr = np.where(R > R[idx])[0]
		else: pfr = np.where(R < R[idx])[0]
		mask[pfr] = True
		
		q_hat, q0tmp = self.set_layer(psimin, lq, S, lcfs = self.lcfs, lobes = True)
		if np.sum(mask > 0): q_hat[mask],_ = self.set_layer(psimin[mask], lq, S, q0 = q0tmp)
		
		#Menard's method
		psiN = self.ep.psiFunc.ev(R,Z)	# this is normalized
		psi = psiN * (self.ep.g['psiSep']-self.ep.g['psiAxis']) + self.ep.g['psiAxis']	# this is flux
		P0 = 2*np.pi * integ.simps(q_hat, psi)
		#account for nonphysical power
		if P0 < 0: P0 = -P0
		#Scale to input power
		q0 = P/P0
		return q0 #, q_hat,R,psiN,psi


	def fluxConversion(self, R):
		"""
		Returns the transformation factor xfm between the midplane distance s and the divertor flux psi.
		This also accounts for the flux expansion.
		"""
		if isinstance(R, np.ndarray): Z = self.ep.g['ZmAxis']*np.ones(R.shape)
		else: Z = self.ep.g['ZmAxis']
		Bp = self.ep.BpFunc.ev(R,Z)
		deltaPsi = np.abs(self.ep.g['psiSep'] - self.ep.g['psiAxis'])
		gradPsi = Bp*R
		xfm = gradPsi / deltaPsi
		return xfm
		
	
	def scale_layer2D(self, lq, S, P, HFS = False):
		"""
		scales HF using a 2D profile, as if lcfs = 1.0
		Get scale factor q||0 (q0) for heat flux via power balance:
		(input MW = output MW)
		Ignores wall psi and just creates a profile at OMP
		Creates a dense (1000pts) grid at the midplane to get higher resolution
		integral.  Integrates q_hat with respect to psi.
		q||0 = P_div / ( 2*pi* integral(q_hat dPsi ))
		return q0
		"""		
		# Get a psi range that fully covers the profile for integration. Peak location does not matter, so use s0 from psi = 1.0
		if HFS:
			Rsep = self.ep.g['lcfs'][:,0].min()
			Rmin = Rsep - 20.0*lq*(1e-3)		#in m
			if Rmin < min(self.ep.g['R']): Rmin = min(self.ep.g['R'])	#if Rmin outside EFIT grid, cap at minimum R of grid
			Rmax = Rsep + 20.0*S*(1e-3)		#in m
			if Rmax > self.ep.g['RmAxis']: Rmax = self.ep.g['RmAxis']	#if Rmax is outside the magnetic axis, psi would increase again, so cap at axis
		else:
			Rsep = self.ep.g['lcfs'][:,0].max()
			Rmin = Rsep - 20.0*S*(1e-3)		#in m
			if Rmin < self.ep.g['RmAxis']: Rmin = self.ep.g['RmAxis']	#if Rmin is inside the magnetic axis, psi would increase again, so cap at axis
			Rmax = Rsep + 20.0*lq*(1e-3)		#in m
			if Rmax > max(self.ep.g['R']): Rmax = max(self.ep.g['R'])	#if Rmax is outside EFIT grid, cap at maximum R of grid

		R = np.linspace(Rmin,Rmax,1000)
		Z = self.ep.g['ZmAxis']*np.ones(R.shape)
		psiN = self.ep.psiFunc.ev(R,Z)	# this is normalized
		
		xfm = self.fluxConversion(R)
		q_hat = eich_profile(psiN, lq, S, 1.0, q0 = 1, qBG = 0, fx = xfm)	# becomes profile of psi by using xfm factor
		
		#Menard's method
		psi = psiN * (self.ep.g['psiSep']-self.ep.g['psiAxis']) + self.ep.g['psiAxis']	# this is flux
		P0 = 2*np.pi * integ.simps(q_hat, psi)
		#account for nonphysical power
		if P0 < 0: P0 = -P0
		#Scale to input power
		q0 = P/P0
		return q0


#==========================================================================================================================
#   general functions
#==========================================================================================================================

def Tprofile(psi, Tped, deriv = False):
	"""
	A default T profile, used only if no T profile data is provided
	Input:
	  psi = normalized poloidal flux
	  Tped = temperature at top of pedestal in keV
	  deriv = bool, return derivative of profile, default is false
	Return:
	  T (,dT) = T(psi) profile (, derivative of profile)
	"""
	xs = 0.975		# Symmetry point in Pedestal
	dw = 0.04		# half of Pedestal width

	f = lambda x: 0.5*np.tanh(2*(xs - x)/dw) + 2*np.exp(-x*2)
	T0 = Tped/(f(xs-dw) - f(1.2))
	T = T0*(f(psi) - f(1.2))
	if deriv:
		dT = -T0/dw*(1 - np.tanh(2*(xs - psi)/dw)**2) - T0*4*np.exp(-psi*2)
		return T, dT
	else: return T


def setAllTypes(obj, integers, floats, bools):
	"""
	Set data types for variales in obj
	"""
	for var in integers:
		if (getattr(obj, var) is not None) and (~np.isnan(float(getattr(obj, var)))):
			try:
				setattr(obj, var, tools.makeInt(getattr(obj, var)))
			except:
				print("Error with input file var "+var+".  Perhaps you have invalid input values?")
				log.info("Error with input file var "+var+".  Perhaps you have invalid input values?")
	for var in floats:
		if var is not None:
			if (getattr(obj, var) is not None) and (~np.isnan(float(getattr(obj, var)))):
				try:
					setattr(obj, var, tools.makeFloat(getattr(obj, var)))
				except:
					print("Error with input file var "+var+".  Perhaps you have invalid input values?")
					log.info("Error with input file var "+var+".  Perhaps you have invalid input values?")
	for var in bools:
		try:
			setattr(obj, var, tools.makeBool(getattr(obj, var)))
		except:
			print("Error with input file var "+var+".  Perhaps you have invalid input values?")
			log.info("Error with input file var "+var+".  Perhaps you have invalid input values?")


def eich_profile(s, lq, S, s0, q0, qBG = 0, fx = 1):
	"""
	Based on the paper: T.Eich et al.,PRL 107, 215001 (2011)
	lq is heat flux width at midplane in mm
	S is the private flux region spreading in mm
	s0 is the separatrix location at Z = 0 in m
	q0 is the amplitude
	qBG is the background heat flux
	fx is the flux expansion between outer midplane and target plate
	s is in m
	return function q(s)
	
	in Eich paper: s and s0 are distances along target, mapped from midplane using flux expansion fx,
	so: s = s_midplane * fx; same for s0, with s0 the position of strikeline on target
	Here, use s_midplane directly, so set fx = 1 and identify s = s_midplane = R and s0 = Rsep
	"""
	from scipy.special import erfc
	lq *= 1e-3		# in m now
	S *= 1e-3		# in m now
	a = lq*fx
	b = 0.5*S/lq
	c = S*fx
	q = 0.5 * q0 * np.exp(b**2 - (s-s0)/a) * erfc(b - (s-s0)/c) + qBG
	return q


def readShadowFile(f, PFC):
	"""
	read shadowMask.csv file
	"""
	#f = base + self.tsFmt.format(t) + '/' + PFC.name + '/shadowMask.csv
	try:
		df = pd.read_csv(f, names=['X','Y','Z','shadowMask'], skiprows=[0])
		if len(df['shadowMask'].values) != len(PFC.centers):
			print('shadowMask file mesh is not same length as STL file mesh.')
			print('Will not assign shadowMask to mismatched mesh')
			print("File length: {:d}".format(len(df['shadowMask'].values)))
			print("PFC STL mesh length: {:d}".format(len(PFC.centers)))
			val = -1
		else:
			PFC.shadowed_mask = df['shadowMask'].values
			print("Loaded Shadow Mask from file: "+f)
			val = 0
	except:
		print("COULD NOT READ FILE: "+f)
		print("Please point HEAT to a valid file,")
		print("which should be a .csv file with (X,Y,Z,shadowMask)")
		val = -1

	return val


