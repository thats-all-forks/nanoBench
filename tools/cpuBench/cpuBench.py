#!/usr/bin/python
import xml.etree.ElementTree as ET
from xml.etree.ElementTree import Element, SubElement, Comment, tostring
from xml.dom import minidom
from itertools import groupby, cycle, islice, chain
from collections import namedtuple, OrderedDict

import argparse
import copy
import datetime
import math
import os
import re
import subprocess
import sys
import logging
import pickle
import shutil
import tarfile

from utils import *

sys.path.append('../..')
from kernelNanoBench import *

sys.path.append('../CPUID')
import cpuid

useIACA=False
iacaCMDLine = ''
iacaVersion = ''
arch = ''
debugOutput = False
supportsAVX = False
instrNodeList = [] # list of all XML instruction nodes that are not filtered out
instrNodeDict = {} # dict from instrNode.attrib['string'] to instrNode

globalDoNotWriteRegs = {'R13', 'R13D', 'R13W', 'R13B', 'R14', 'R14D', 'R14W', 'R14B', 'R15', 'R15D', 'R15W', 'R15B', 'SP', 'SPL', 'ESP', 'RSP', 'XMM13', 'YMM13', 'ZMM13', 'XMM14', 'YMM14', 'ZMM14', 'XMM15', 'YMM15', 'ZMM15', 'MM15', 'IP', 'DR4', 'DR5', 'DR6', 'DR7', 'RBP', 'EBP', 'BP', 'K0'} #ToDo
#R14: reserved for memory addresses
#R13: can be written in init; will not be overwritten by other code
#R15: loop counter

specialRegs = {'ES', 'CS', 'SS', 'DS', 'FS', 'GS', 'IP', 'EIP', 'FSBASEy', 'GDTR', 'GSBASEy', 'IDTR', 'IP', 'LDTR', 'MSRS', 'MXCSR', 'RFLAGS', 'RIP',
   'TR', 'TSC', 'TSCAUX', 'X87CONTROL', 'X87POP', 'X87POP2', 'X87PUSH', 'X87STATUS', 'X87TAG', 'XCR0', 'XMM0dq', 'CR0', 'CR2', 'CR3', 'CR4', 'CR8', 'ERROR',
   'BND0', 'BND1', 'BND2', 'BND3'}

GPRRegs = {'AH', 'AL', 'AX', 'BH', 'BL', 'BP', 'BPL', 'BX', 'CH', 'CL', 'CX', 'DH', 'DI', 'DIL', 'DL', 'DX', 'EAX',
   'EBP', 'EBX', 'ECX', 'EDI', 'EDX', 'ESI', 'ESP', 'R10', 'R10B', 'R10D', 'R10W', 'R11', 'R11B', 'R11D', 'R11W', 'R12',
   'R12B', 'R12D', 'R12W', 'R13', 'R13B', 'R13D', 'R13W', 'R14', 'R14B', 'R14D', 'R14W', 'R15', 'R15B', 'R15D', 'R15W',
   'R8', 'R8B', 'R8D', 'R8W', 'R9', 'R9B', 'R9D', 'R9W', 'RAX', 'RBP', 'RBX', 'RCX', 'RDI', 'RDX', 'RSI', 'RSP', 'SI',
   'SIL', 'SP', 'SPL'}

STATUSFLAGS = {'CF', 'PF', 'AF', 'ZF', 'SF', 'OF'}
STATUSFLAGS_noAF = {'CF', 'PF', 'ZF', 'SF', 'OF'}

maxTPRep = 16

#iforms of serializing and memory-ordering instructions according to Ch. 8.3 of the Intel manual
serializingInstructions = {'INVD', 'INVEPT', 'INVLPG', 'INVVPID', 'LGDT', 'LIDT', 'LLDT', 'LTR', 'MOV_CR_CR_GPR64', 'MOV_DR_DR_GPR64', 'WBINVD', 'WRMSR',
                           'CPUID', 'IRET', 'RSM', 'SFENCE', 'LFENCE', 'MFENCE'}

def isAMDCPU():
   return arch in ['ZEN+', 'ZEN2']

def isIntelCPU():
   return not isAMDCPU()

def regTo64(reg):
   if 'AX' in reg or 'AH' in reg or 'AL' in reg: return 'RAX'
   if 'BX' in reg or 'BH' in reg or 'BL' in reg: return 'RBX'
   if 'CX' in reg or 'CH' in reg or 'CL' in reg: return 'RCX'
   if 'DX' in reg or 'DH' in reg or 'DL' in reg: return 'RDX'
   if 'SP' in reg: return 'RSP'
   if 'BP' in reg: return 'RBP'
   if 'SI' in reg: return 'RSI'
   if 'DI' in reg: return 'RDI'
   if '8' in reg: return 'R8'
   if '9' in reg: return 'R9'
   if '10' in reg: return 'R10'
   if '11' in reg: return 'R11'
   if '12' in reg: return 'R12'
   if '13' in reg: return 'R13'
   if '14' in reg: return 'R14'
   if '15' in reg: return 'R15'

def regTo32(reg):
   if 'AX' in reg or 'AH' in reg or 'AL' in reg: return 'EAX'
   if 'BX' in reg or 'BH' in reg or 'BL' in reg: return 'EBX'
   if 'CX' in reg or 'CH' in reg or 'CL' in reg: return 'ECX'
   if 'DX' in reg or 'DH' in reg or 'DL' in reg: return 'EDX'
   if 'SP' in reg: return 'ESP'
   if 'BP' in reg: return 'EBP'
   if 'SI' in reg: return 'ESI'
   if 'DI' in reg: return 'EDI'
   if '8' in reg: return 'R8D'
   if '9' in reg: return 'R9D'
   if '10' in reg: return 'R10D'
   if '11' in reg: return 'R11D'
   if '12' in reg: return 'R12D'
   if '13' in reg: return 'R13D'
   if '14' in reg: return 'R14D'
   if '15' in reg: return 'R15D'

def regTo16(reg):
   if 'AX' in reg or 'AH' in reg or 'AL' in reg: return 'AX'
   if 'BX' in reg or 'BH' in reg or 'BL' in reg: return 'BX'
   if 'CX' in reg or 'CH' in reg or 'CL' in reg: return 'CX'
   if 'DX' in reg or 'DH' in reg or 'DL' in reg: return 'DX'
   if 'SP' in reg: return 'SP'
   if 'BP' in reg: return 'BP'
   if 'SI' in reg: return 'SI'
   if 'DI' in reg: return 'DI'
   if '8' in reg: return 'R8W'
   if '9' in reg: return 'R9W'
   if '10' in reg: return 'R10W'
   if '11' in reg: return 'R11W'
   if '12' in reg: return 'R12W'
   if '13' in reg: return 'R13W'
   if '14' in reg: return 'R14W'
   if '15' in reg: return 'R15W'

def regTo8(reg):
   if 'AX' in reg or 'AH' in reg or 'AL' in reg: return 'AL'
   if 'BX' in reg or 'BH' in reg or 'BL' in reg: return 'BL'
   if 'CX' in reg or 'CH' in reg or 'CL' in reg: return 'CL'
   if 'DX' in reg or 'DH' in reg or 'DL' in reg: return 'DL'
   if 'SP' in reg: return 'SPL'
   if 'BP' in reg: return 'BPL'
   if 'SI' in reg: return 'SIL'
   if 'DI' in reg: return 'DIL'
   if '8' in reg: return 'R8B'
   if '9' in reg: return 'R9B'
   if '10' in reg: return 'R10B'
   if '11' in reg: return 'R11B'
   if '12' in reg: return 'R12B'
   if '13' in reg: return 'R13B'
   if '14' in reg: return 'R14B'
   if '15' in reg: return 'R15B'

def regToSize(reg, size):
   if size == 8: return regTo8(reg)
   elif size == 16: return regTo16(reg)
   elif size == 32: return regTo32(reg)
   else: return regTo64(reg)

# Returns a set of registers that are a part of the register that is provided (e.g., EAX is a part of RAX; RAX is also a part of RAX)
def getSubRegs(reg):
   subRegs = set()
   subRegs.add(reg)
   if reg in GPRRegs:
      regSize = getRegSize(reg)
      if regSize > 8:
         for size in [16, 32, 64]:
            if size > regSize: continue
            subRegs.add(regToSize(reg, size))
         if 'AX' in reg or 'BX' in reg or 'CX' in reg or 'DX' in reg:
            subRegs.add(reg[-2] + 'L')
            subRegs.add(reg[-2] + 'H')
         else:
            subRegs.add(regTo8(reg))
   elif 'ZMM' in reg:
      subRegs.add('Y' + reg[1:])
      subRegs.add('X' + reg[1:])
   elif 'YMM' in reg:
      subRegs.add('X' + reg[1:])
   return subRegs

# Returns for a GPR the corresponding 64-bit registers, and for a (X|Y|Z)MM register the corresponding XMM register
def getCanonicalReg(reg):
   if reg in GPRRegs:
      return regTo64(reg)
   elif 'MM' in reg:
      return re.sub('^[YZ]', 'X', reg)
   else:
      return reg

def getRegForMemPrefix(reg, memPrefix):
   return regToSize(reg, getSizeOfMemPrefix(memPrefix))

def getSizeOfMemPrefix(memPrefix):
   if 'zmmword' in memPrefix: return 512
   elif 'ymmword' in memPrefix: return 256
   elif 'xmmword' in memPrefix: return 128
   elif 'qword' in memPrefix: return 64
   elif 'dword' in memPrefix: return 32
   elif 'word' in memPrefix: return 16
   elif 'byte' in memPrefix: return 8
   else: return -1

def getRegSize(reg):
   if reg[-1] == 'L' or reg[-1] == 'H' or reg[-1] == 'B': return 8
   elif reg[-1] == 'W' or reg in ['AX', 'BX', 'CX', 'DX', 'SP', 'BP' 'SI', 'DI']: return 16
   elif reg[0] == 'E' or reg[-1] == 'D': return 32
   elif reg in GPRRegs: return 64
   elif reg.startswith('MM'): return 64
   elif reg.startswith('XMM'): return 128
   elif reg.startswith('YMM'): return 256
   elif reg.startswith('ZMM'): return 512
   else: return -1

def getAddrReg(instrNode, opNode):
   if opNode.attrib.get('suppressed', '0') == '1':
      return opNode.attrib['base']
   elif instrNode.attrib.get('rex', '1') == '0':
      return 'RDI'
   else:
      return 'R14'

# registers that are not used as implicit registers should come first; RAX (and parts of it) should come last, as some instructions have special encodings for that
# prefer low registers to high registers
def sortRegs(regsList):
   return sorted(regsList, key=lambda r: (not any(i.isdigit() for i in r), 'H' in r, 'A' in r, map(int, re.findall('\d+',r)), r))


# Initialize registers and memory
def getRegMemInit(instrNode, opRegDict, memOffset):
   iform = instrNode.attrib['iform']
   iclass = instrNode.attrib['iclass']

   init = []

   if iform == 'CLZERO': init += ['MOV RAX, R14']
   if iclass == 'LDMXCSR': init += ['STMXCSR [R14+' + str(memOffset) + ']']
   if iclass == 'VLDMXCSR': init += ['VSTMXCSR [R14+' + str(memOffset) + ']']
   if iform == 'LGDT_MEMs64': init += ['SGDT [R14+' + str(memOffset) + ']']
   if iform == 'LIDT_MEMs64': init += ['SIDT [R14+' + str(memOffset) + ']']
   if iform == 'LLDT_MEMw': init += ['SLDT [R14+' + str(memOffset) + ']']
   if iform == 'XLAT': init += ['MOV RBX, R14', 'mov qword ptr [RBX], 0']

   if (isSSEInstr(instrNode) or isAVXInstr(instrNode)) and supportsAVX:
      # Zero upper bits to avoid AVX-SSE transition penalties; also, e.g., dep. breaking and zero-latency instructions do not seem to work otherwise
      # we use vzeroall instead of just vzeroupper to make sure that XMM14 is 0 for VSIB addressing
      init += ['VZEROALL']

   if not 'DIV' in instrNode.attrib['iclass'] and not 'SQRT' in instrNode.attrib['iclass']:
      for opNode in instrNode.findall('./operand[@r="1"]'):
         opIdx = int(opNode.attrib['idx'])
         xtype = opNode.attrib.get('xtype', '')

         if opNode.attrib['type'] == 'reg':
            reg = opRegDict[opIdx]
            regPrefix = re.sub('\d', '', reg)

            if 'MM' in regPrefix and xtype.startswith('f'):
               init += ['MOV RAX, 0x4000000040000000']
               for i in range(0, getRegSize(reg)/8, 8): init += ['MOV [R14+' + str(i) + '], RAX']

               if isAVXInstr(instrNode):
                  init += ['VMOVUPD ' + reg + ', [R14]']
               else:
                  init += ['MOVUPD ' + reg + ', [R14]']
            elif regPrefix in ['XMM', 'YMM', 'ZMM'] and isAVXInstr(instrNode):
               # some AVX instr. (e.g. VORPS, VAESDEC) incur a penalty (?) if a source was not written by an AVX instr. of a similar kind
               if reg not in globalDoNotWriteRegs:
                  for opNode2 in instrNode.findall('./operand[@w="1"]'):
                     if not opNode2.text == opNode.text: continue
                     init += [getInstrInstanceFromNode(instrNode, opRegDict={int(opNode2.attrib['idx']):reg}, computeRegMemInit=False).asm]
                     break
                  else:
                     init += ['VXORPS '+reg+', '+reg+', '+reg]
            elif 'MM' in regPrefix:
               init += ['PXOR '+reg+', '+reg]
         elif opNode.attrib['type'] == 'mem':
            if 'VSIB' in opNode.attrib:
               vsibReg = opNode.attrib['VSIB'] + '14'
               init += ['VXORPS ' + vsibReg + ', ' + vsibReg + ', ' + vsibReg]
            if xtype.startswith('f'):
               init += ['MOV RAX, 0x4000000040000000']
               for i in range(0, int(opNode.attrib['width'])/8, 8): init += ['MOV [R14+' + str(i+memOffset) + '], RAX']

   return init

nExperiments = 0
def runExperiment(instrNode, instrCode, init=None, unrollCount=500, loopCount=0, warmUpCount=10, basicMode=False, htmlReports=None, maxRepeat=1):
   # we use a default warmUpCount of 10, as ICL requires at least about that much before memory operations run at full speed

   if init is None: init = []
   localHtmlReports = []

   global nExperiments
   nExperiments += 1

   instrCode = re.sub(';+', '; ', instrCode.strip('; '))
   if debugOutput: print 'instr: ' + instrCode
   codeObjFile = '/tmp/ramdisk/code.o'
   assemble(instrCode, codeObjFile, asmFile='/tmp/ramdisk/code.s')
   localHtmlReports.append('<li>Code: <pre>' + getMachineCode(codeObjFile) + '</pre></li>\n')

   init = list(OrderedDict.fromkeys(init)) # remove duplicates while maintaining the order
   initCode = '; '.join(init)

   if instrNode is not None and (instrNode.attrib.get('vex', '') == '1' or instrNode.attrib.get('evex', '') == '1'):
      # vex and evex encoded instructions need a warm-up period before memory reads operate at full speed;
      # https://software.intel.com/en-us/forums/intel-isa-extensions/topic/710248
      reg = 'ZMM' if 'ZMM' in instrNode.attrib['iform'] else 'YMM'
      # the instruction needs to be used at least twice in the body of the loop
      # putting it to one_time_init is not sufficient, independently of the loop count, example:
      # "VPTEST YMM0, YMM1;CMOVZ R13, R15; VPBROADCASTQ ZMM0, R13" on CNL
      avxInitCode = 'MOV R15, 10000; L: VADDPS {0}, {1}, {1}; VADDPS {0}, {1}, {1}; DEC R15; JNZ L; '.format(reg + '0', reg + '1')
      initCode = avxInitCode + initCode

   nanoBenchCmd = 'sudo ./kernel-nanoBench.sh'
   nanoBenchCmd += ' -unroll ' + str(unrollCount)
   if loopCount > 0: nanoBenchCmd += ' -loop ' + str(loopCount)
   if basicMode: nanoBenchCmd += ' -basic'
   nanoBenchCmd += ' -warm_up_count ' + str(warmUpCount)
   nanoBenchCmd += ' -asm &quot;' + instrCode + '&quot;'

   initObjFile = None
   if initCode:
      if debugOutput: print 'init: ' + initCode
      initObjFile = '/tmp/ramdisk/init.o'
      assemble(initCode, initObjFile, asmFile='/tmp/ramdisk/init.s')
      localHtmlReports.append('<li>Init: <pre>' + re.sub(';[ \t]*(.)', r';\n\1', initCode) + '</pre></li>\n')
      nanoBenchCmd += ' -asm_init &quot;' + initCode + '&quot;'

   localHtmlReports.append('<li><a href="javascript:;" onclick="this.outerHTML = \'<pre>' + nanoBenchCmd + '</pre>\'">Show nanoBench command</a></li>\n')

   setNanoBenchParameters(unrollCount=unrollCount, loopCount=loopCount, warmUpCount=warmUpCount, basicMode=basicMode)

   ret = runNanoBench(codeObjFile=codeObjFile, initObjFile=initObjFile)

   localHtmlReports.append('<li>Results:\n<ul>\n')
   for evt, value in ret.items():
      if 'RDTSC' in evt: continue
      if evt == 'UOPS':
         if arch in ['CON', 'WOL']: evt = 'RS_UOPS_DISPATCHED'
         elif arch in ['NHM', 'WSM']: evt = 'UOPS_RETIRED.ANY'
         elif arch in ['SNB', 'IVB', 'HSW', 'BDW']: evt = 'UOPS_RETIRED.ALL'
         elif arch in ['SKL', 'SKX', 'KBL', 'CFL', 'CNL', 'ICL']: evt = 'UOPS_EXECUTED.THREAD'
      localHtmlReports.append('<li>' + evt + ': ' + str(value) + '</li>\n')
   localHtmlReports.append('</ul>\n</li>')

   if arch in ['NHM', 'WSM'] and 'UOPS_PORT3' in ret:
      # Workaround for broken port4 and port5 counters
      ret['UOPS_PORT4'] = ret['UOPS_PORT3']
      ret['UOPS_PORT5'] = max(0, ret['UOPS'] - ret['UOPS_PORT0'] - ret['UOPS_PORT1'] - ret['UOPS_PORT2'] - ret['UOPS_PORT3'] - ret['UOPS_PORT4'])

   if isAMDCPU():
      ret['Core cycles'] = ret['APERF']

   if maxRepeat>0:
      if any(v<-0.05 for v in ret.values()):
         print 'Repeating experiment because there was a value < 0'
         return runExperiment(instrNode, instrCode, init=init, unrollCount=unrollCount, loopCount=loopCount, basicMode=basicMode, htmlReports=htmlReports, maxRepeat=maxRepeat-1)

      #sumPortUops = sum(v for e,v in ret.items() if 'PORT' in e and not '4' in e)
      #if (sumPortUops % 1) > .2 and (sumPortUops % 1) < .8:
      #   print 'Repeating experiment because the sum of the port usages is not an integer'
      #   print ret
      #   return runExperiment(instrNode, instrCode, init=init, unrollCount=unrollCount, loopCount=loopCount, basicMode=basicMode, htmlReports=htmlReports, maxRepeat=maxRepeat-1)

      if any('PORT' in e for e in ret):
         maxPortUops = max(v/(len(e)-9) for e,v in ret.items() if 'PORT' in e)
         if maxPortUops * .98 > ret['Core cycles']:
            print 'Repeating experiment because there were more uops on a port than core cycles'
            return runExperiment(instrNode, instrCode, init=init, unrollCount=unrollCount, loopCount=loopCount, basicMode=basicMode, htmlReports=htmlReports, maxRepeat=maxRepeat-1)

   if htmlReports is not None:
      htmlReports.extend(localHtmlReports)
   return ret


def writeFile(fileName, content):
   with open(fileName, "w") as f:
      f.write(content+"\n");


def getMachineCode(objFile):
   try:
      machineCode = subprocess.check_output(['objdump', '-M', 'intel', '-d', objFile])
      return machineCode.partition('<.text>:\n')[2]
   except subprocess.CalledProcessError as e:
      print "Error (getMachineCode): " + str(e)


def getEventConfig(event):
   if event == 'UOPS':
      if arch in ['CON', 'WOL']: return 'A0.00' # RS_UOPS_DISPATCHED
      if arch in ['NHM', 'WSM', 'SNB', 'IVB', 'HSW', 'BDW']: return 'C2.01' # UOPS_RETIRED.ALL
      if arch in ['SKL', 'SKX', 'KBL', 'CFL', 'CNL', 'ICL']: return 'B1.01' # UOPS_EXECUTED.THREAD
      if arch in ['ZEN+', 'ZEN2']: return '0C1.00'
   if event == 'RETIRE_SLOTS':
      if arch in ['NHM', 'WSM', 'SNB', 'IVB', 'HSW', 'BDW', 'SKL', 'SKX', 'KBL', 'CFL', 'CNL', 'ICL']: return 'C2.02'
   if event == 'UOPS_PORT0':
      if arch in ['CON', 'WOL']: return 'A1.01.CTR=0'
      if arch in ['NHM', 'WSM']: return 'B1.01'
      if arch in ['SNB', 'IVB', 'HSW', 'BDW', 'SKL', 'SKX', 'KBL', 'CFL', 'CNL', 'ICL']: return 'A1.01'
   if event == 'UOPS_PORT1':
      if arch in ['CON', 'WOL']: return 'A1.02.CTR=0'
      if arch in ['NHM', 'WSM']: return 'B1.02'
      if arch in ['SNB', 'IVB', 'HSW', 'BDW', 'SKL', 'SKX', 'KBL', 'CFL', 'CNL', 'ICL']: return 'A1.02'
   if event == 'UOPS_PORT2':
      if arch in ['CON', 'WOL']: return 'A1.04.CTR=0'
      if arch in ['NHM', 'WSM']: return 'B1.04'
      if arch in ['SNB', 'IVB']: return 'A1.0C'
      if arch in ['HSW', 'BDW', 'SKL', 'SKX', 'KBL', 'CFL', 'CNL']: return 'A1.04'
   if event == 'UOPS_PORT3':
      if arch in ['CON', 'WOL']: return 'A1.08.CTR=0'
      if arch in ['NHM', 'WSM']: return 'B1.08'
      if arch in ['SNB', 'IVB']: return 'A1.30'
      if arch in ['HSW', 'BDW', 'SKL', 'SKX', 'KBL', 'CFL', 'CNL']: return 'A1.08'
   if event == 'UOPS_PORT4':
      if arch in ['CON', 'WOL']: return 'A1.10.CTR=0'
      if arch in ['NHM', 'WSM']: return 'B1.10'
      if arch in ['SNB', 'IVB']: return 'A1.40'
      if arch in ['HSW', 'BDW', 'SKL', 'SKX', 'KBL', 'CFL', 'CNL']: return 'A1.10'
   if event == 'UOPS_PORT5':
      if arch in ['CON', 'WOL']: return 'A1.20.CTR=0'
      if arch in ['NHM', 'WSM']: return 'B1.20'
      if arch in ['SNB', 'IVB']: return 'A1.80'
      if arch in ['HSW', 'BDW', 'SKL', 'SKX', 'KBL', 'CFL', 'CNL', 'ICL']: return 'A1.20'
   if event == 'UOPS_PORT6':
      if arch in ['HSW', 'BDW', 'SKL', 'SKX', 'KBL', 'CFL', 'CNL', 'ICL']: return 'A1.40'
   if event == 'UOPS_PORT7':
      if arch in ['HSW', 'BDW', 'SKL', 'SKX', 'KBL', 'CFL', 'CNL']: return 'A1.80'
   if event == 'UOPS_PORT23':
      if arch in ['ICL']: return 'A1.04'
   if event == 'UOPS_PORT49':
      if arch in ['ICL']: return 'A1.10'
   if event == 'UOPS_PORT78':
      if arch in ['ICL']: return 'A1.80'
   if event == 'DIV_CYCLES':
      if arch in ['NHM', 'WSM', 'SNB', 'IVB', 'HSW', 'BDW', 'SKL', 'SKX', 'KBL', 'CFL', 'CNL']: return '14.01.CMSK=1' # undocumented on HSW, but seems to work
      if arch in ['ICL']: return '14.09.CMSK=1'
      if arch in ['ZEN+', 'ZEN2']: return '0D3.00'
   if event == 'ILD_STALL.LCP':
      if arch in ['NHM', 'WSM', 'SNB', 'IVB', 'HSW', 'BDW', 'SKL', 'SKX', 'KBL', 'CFL', 'CNL', 'ICL']: return '87.01'
   if event == 'INST_DECODED.DEC0':
      if arch in ['NHM', 'WSM']: return '18.01'
   if event == 'FpuPipeAssignment.Total0':
      if arch in ['ZEN+', 'ZEN2']: return '000.01'
   if event == 'FpuPipeAssignment.Total1':
      if arch in ['ZEN+', 'ZEN2']: return '000.02'
   if event == 'FpuPipeAssignment.Total2':
      if arch in ['ZEN+', 'ZEN2']: return '000.04'
   if event == 'FpuPipeAssignment.Total3':
      if arch in ['ZEN+', 'ZEN2']: return '000.08'
   return None


def configurePFCs(events):
   content = ''
   for event in events:
      cfg = getEventConfig(event)
      if cfg is not None:
         content += cfg + ' ' + event + '\n'
   setNanoBenchParameters(config=content)


InstrInstance = namedtuple('InstrInstance', ['instrNode', 'asm', 'readRegs', 'writtenRegs', 'opRegDict', 'regMemInit'])

def getInstrInstanceFromNode(instrNode, doNotWriteRegs=None, doNotReadRegs=None, useDistinctRegs=True, opRegDict=None, memOffset=0, immediate=2, computeRegMemInit=True):
   if not doNotWriteRegs: doNotWriteRegs = []
   if not doNotReadRegs: doNotReadRegs = []
   if not opRegDict: opRegDict = {}

   if instrNode.attrib['extension'] == 'AVX2GATHER': useDistinctRegs=True

   readRegs = set()
   writtenRegs = set()
   opRegDict = dict(opRegDict)

   for operandNode in instrNode.iter('operand'):
      if operandNode.attrib['type'] == "reg":
         regsList = sortRegs(operandNode.text.split(','))
         if len(regsList) == 1:
            reg = regsList[0]
            opRegDict[int(operandNode.attrib['idx'])] = reg
            if operandNode.attrib.get('w', '0') == '1':
               writtenRegs.add(reg)
            if operandNode.attrib.get('r', '0') == '1':
               readRegs.add(reg)
      elif operandNode.attrib['type'] == "mem" and 'base' in operandNode.attrib:
         readRegs.add(operandNode.attrib['base'])

   asm = instrNode.attrib['asm']

   first = True
   for operandNode in instrNode.iter('operand'):
      opI = int(operandNode.attrib['idx'])

      if operandNode.attrib.get('suppressed', '0') == '1':
         continue;

      if not first and not operandNode.attrib.get('opmask', '') == '1':
         asm += ", "
      else:
         asm += " "
         first=False;

      if operandNode.attrib['type'] == "reg":
         if opI in opRegDict:
            reg = opRegDict[opI]
         else:
            regsList = operandNode.text.split(',')

            if len(regsList) > 1:
               ignoreRegs = set()
               if operandNode.attrib.get('w', '0') == '1':
                  ignoreRegs |= set(doNotWriteRegs)|globalDoNotWriteRegs|(set(opRegDict.values()) if useDistinctRegs else set(doNotReadRegs))
               if operandNode.attrib.get('r', '0') == '1':
                  ignoreRegs |= set(doNotReadRegs)|(writtenRegs|readRegs|set(opRegDict.values()) if useDistinctRegs else set(doNotWriteRegs)|globalDoNotWriteRegs)
               regsList = filter(lambda x: not any(y in ignoreRegs for y in getSubRegs(x)) and not (x in [z for y in ignoreRegs for z in getSubRegs(y)]), regsList)
            if not regsList:
               return None;

            reg = sortRegs(regsList)[0];
            if not useDistinctRegs:
               for oReg in opRegDict.values():
                  if oReg in regsList:
                     reg = oReg;
                     break

            opRegDict[opI] = reg
         if operandNode.attrib.get('w', '0') == '1':
            writtenRegs.add(reg)
         if operandNode.attrib.get('r', '0') == '1':
            readRegs.add(reg)

         if not operandNode.attrib.get('opmask', '') == '1':
            asm += reg
         else:
            asm += ' {' + reg + '}'
            if instrNode.attrib.get('zeroing', '') == '1':
               asm += '{z}'
      elif operandNode.attrib['type'] == "mem":
         asmprefix = operandNode.attrib.get('memory-prefix', '')
         asm += asmprefix
         if asmprefix != '':
            asm += ' '

         address = ''
         if operandNode.attrib.get('VSIB', '0') != "0":
            address = 'R14+' + operandNode.attrib.get('VSIB') + '14'
            readRegs.add('R14')
            readRegs.add(operandNode.attrib.get('VSIB') + '14')
         else:
            address = getAddrReg(instrNode, operandNode)
            readRegs.add(address)

         asm += '[' + address + ('+'+str(memOffset) if memOffset else '') + ']'

         memorySuffix = operandNode.attrib.get('memory-suffix', '')
         if memorySuffix:
            asm += ' ' + memorySuffix
      elif operandNode.attrib['type'] == 'agen':
         agen = instrNode.attrib['agen']
         address = []

         if 'R' in agen: address.append('RIP')
         if 'B' in agen: address.append('R14')
         if 'I' in agen: address.append('2*R13')
         if 'D' in agen: address.append('8')

         asm += ' [' + '+'.join(address) + ']'
      elif operandNode.attrib['type'] == "imm":
         if instrNode.attrib.get('roundc', '') == '1':
            asm += '{rn-sae}, '
         elif instrNode.attrib.get('sae', '') == '1':
            asm += '{sae}, '
         width = int(operandNode.attrib['width'])
         if operandNode.text:
            imm = operandNode.text
         elif (width == 8 or instrNode.attrib['iclass'] in ['ENTER', 'RET_FAR', 'RET_NEAR']):
            imm = immediate
         else:
            imm = 1 << (width-8)
         asm += str(imm)
      elif operandNode.attrib['type'] == "relbr":
         asm += "1f"

   if not 'sae' in asm:
      if instrNode.attrib.get('roundc', '') == '1':
         asm += ', {rn-sae}'
      elif instrNode.attrib.get('sae', '') == '1':
         asm += ', {sae}'

   if '1f' in asm:
      asm = asm + '; 1: '

   regMemInit = []
   if computeRegMemInit: regMemInit = getRegMemInit(instrNode, opRegDict, memOffset)
   return InstrInstance(instrNode, asm, readRegs, writtenRegs, opRegDict, regMemInit)

def createIacaAsmFile(fileName, prefixInstr, prefixRep, instr):
   asm = '.intel_syntax noprefix\n .byte 0x0F, 0x0B; mov ebx, 111; .byte 0x64, 0x67, 0x90\n'
   if prefixInstr:
      for i in xrange(prefixRep):
         asm += prefixInstr + "\n"
   asm += instr + "\n"
   asm += "1:\n"
   asm += 'mov ebx, 222; .byte 0x64, 0x67, 0x90; .byte 0x0F, 0x0B\n'
   writeFile(fileName, asm)


def getUopsOnBlockedPorts(instrNode, useDistinctRegs, blockInstrNode, blockInstrRep, blockedPorts, config, htmlReports):
   instrInstance = config.independentInstrs[0]
   instr = instrInstance.asm
   readRegs = instrInstance.readRegs
   writtenRegs = instrInstance.writtenRegs

   if debugOutput: print '  instr: ' + instr + 'rR: ' + str(readRegs) + ', wR: ' + str(writtenRegs)
   blockInstrsList = getIndependentInstructions(blockInstrNode, True, writtenRegs|readRegs, writtenRegs|readRegs, 64)
   if debugOutput: print '  bIL: ' + str(blockInstrsList)

   htmlReports.append('<hr><h3>With blocking instructions for port' +
                     ('s {' if len(blockedPorts)>1 else ' ') +
                     str(list(blockedPorts))[1:-1] +
                     ('}' if len(blockedPorts)>1 else '') + ':</h3>')

   if useIACA:
      createIacaAsmFile("/tmp/ramdisk/asm.s", ';'.join(islice(cycle(x.asm for x in blockInstrsList), blockInstrRep)), 1, instr)

      try:
         subprocess.check_output(['as', '/tmp/ramdisk/asm.s', '-o', '/tmp/ramdisk/asm.o'])
         iacaOut = subprocess.check_output(iacaCMDLine + (['-analysis', 'THROUGHPUT'] if iacaVersion=='2.1' else []) + ['/tmp/ramdisk/asm.o'], stderr=subprocess.STDOUT)
      except subprocess.CalledProcessError as e:
         print "Error: " + e.output
         return None

      if not iacaOut or ' !' in iacaOut or ' X' in iacaOut or ' 0X' in iacaOut or not 'Total Num Of Uops' in iacaOut:
         print "IACA error"
         return None

      allPortsLine = re.search('\| Cycles \|.*', iacaOut).group(0)
      instrPortsLine = iacaOut.split('\n')[-3]

      allUopsOnBlockedPorts = 0.0
      instrUopsOnBlockedPorts = 0.0

      for p in blockedPorts:
         allPortsCol = allPortsLine.split('|')[p+2].split()
         allUopsOnBlockedPorts += float(allPortsCol[0])

         instrPortsCol = instrPortsLine.split('|')[p+2].split()
         if instrPortsCol:
            instrUopsOnBlockedPorts += float(instrPortsCol[0])

      htmlReports.append('<pre>' + iacaOut + '</pre>')

      if allUopsOnBlockedPorts < blockInstrRep-.5:
         # something went wrong; fewer uops on ports than blockInstrRep
         # happens, e.g., on SKX for ports {0, 1} if AVX-512 is active
         return None

      return int(.2+instrUopsOnBlockedPorts)
   else:

      if isIntelCPU():
         if arch in ['NHM', 'WSM']:
            # Needed for workaround for broken port 5 counter
            events = ['UOPS_PORT'+str(p) for p in range(0,6)] + ['UOPS']
         else:
            events = ['UOPS_PORT'+str(p) for p in blockedPorts]
      else:
         events = ['FpuPipeAssignment.Total0', 'FpuPipeAssignment.Total1', 'FpuPipeAssignment.Total2', 'FpuPipeAssignment.Total3']
      configurePFCs(events)

      blockInstrAsm = ';'.join(islice(cycle(x.asm for x in blockInstrsList), blockInstrRep))

      unrollCount = 1000/blockInstrRep # make sure that instrs. fit into icache
      if isAMDCPU(): unrollCount = max(unrollCount, 100) # ZEN+ sometimes undercounts FP usage if code is short


      init = list(chain.from_iterable([x.regMemInit for x in blockInstrsList])) + instrInstance.regMemInit + config.init

      htmlReports.append('<ul>\n')
      measurementResult = runExperiment(instrNode, blockInstrAsm + ';' + config.preInstrCode + ';' + instr, init=init, unrollCount=unrollCount, htmlReports=htmlReports)
      htmlReports.append('</ul>\n')

      if float(measurementResult['Core cycles']) < -10:
         #something went wrong; this happens for example on HSW with long sequences of JMP instructions
         if debugOutput: print "Core cycles < -10 in getUopsOnBlockedPorts"

      if sum(u for p, u in measurementResult.items() if ('UOPS_PORT' in p or 'FpuPipeAssignment.Total' in p)) < blockInstrRep-.5:
         # something went wrong; fewer uops on ports than blockInstrRep
         # happens, e.g., on SKX for ports {0, 1} if AVX-512 is active
         return None

      if isIntelCPU():
         ports_dict = {int(p[9:]): i for p, i in measurementResult.items() if p.startswith('UOPS_PORT')}
      else:
         ports_dict = {int(p[23:]): i for p, i in measurementResult.items() if 'FpuPipeAssignment.Total' in p}

      return int(.2+sum([uops for p, uops in ports_dict.items() if p in blockedPorts])) - blockInstrRep


# Takes an instrNode and returns a list [instrI, instrI', ...] s.t. instrI(')* are the results of
# calls to getInstrInstanceFromNode for instrNode and there are no read-after-writes of the same regs/memory locations. The length of the list is limited by maxTPRep.
def getIndependentInstructions(instrNode, useDistinctRegs, doNotReadRegs = None, doNotWriteRegs = None, initialOffset = 0, immediate = 2):
   if not doNotReadRegs: doNotReadRegs = set()
   if not doNotWriteRegs: doNotWriteRegs = set()
   doNotReadRegs |= specialRegs
   doNotWriteRegs |= globalDoNotWriteRegs|specialRegs

   for opNode in instrNode.iter('operand'):
      if opNode.attrib['type'] == 'reg':
         regs = sortRegs(opNode.text.split(","))
         if len(regs) == 1:
            doNotReadRegs.add(regs[0])
            doNotWriteRegs.add(regs[0])
         if len(regs) >= 8 and 'RAX' in map(regTo64, regs):
            #avoid RAX register if possible as some instructions have a special encoding for this
            doNotReadRegs.add('RAX')
            doNotWriteRegs.add('RAX')

   independentInstructions = []
   offset = initialOffset

   for _ in range(maxTPRep):
      instrI = getInstrInstanceFromNode(instrNode, doNotWriteRegs, doNotReadRegs, useDistinctRegs, {}, offset, immediate=immediate)
      if not instrI:
         break

      if instrI in independentInstructions:
         break

      maxMemWidth = 0
      for memNode in instrNode.findall('./operand[@type="mem"][@w="1"]'):
         maxMemWidth = max(maxMemWidth, int(memNode.attrib.get('width', '0'))/8)
      offset += maxMemWidth

      independentInstructions.append(instrI)

      doNotWriteRegs = doNotWriteRegs | instrI.writtenRegs | instrI.readRegs
      doNotReadRegs = doNotReadRegs | instrI.writtenRegs

   if not independentInstructions:
      instrI = getInstrInstanceFromNode(instrNode, useDistinctRegs=False, immediate=immediate)
      independentInstructions.append(instrI)

   return independentInstructions

# Returns True iff there are two non-suppressed operands that can use the same register
def hasCommonRegister(instrNode):
   for opNode1 in instrNode.iter('operand'):
      if opNode1.attrib.get('suppressed', '0') == '1': continue
      if not opNode1.attrib['type'] == 'reg': continue
      regs1 = opNode1.text.split(",")
      for opNode2 in instrNode.iter('operand'):
         if opNode1 == opNode2: continue
         if opNode2.attrib.get('suppressed', '0') == '1': continue
         if not opNode2.attrib['type'] == 'reg': continue
         regs2 = opNode2.text.split(",")
         if set(regs1).intersection(set(regs2)):
            return True
   return False

def getThroughputIacaNoInteriteration(instrNode, htmlReports):
   createIacaAsmFile("/tmp/ramdisk/asm.s", "", 0, getInstrInstanceFromNode(instrNode, useDistinctRegs=True).asm)
   try:
      subprocess.check_output(['as', '/tmp/ramdisk/asm.s', '-o', '/tmp/ramdisk/asm.o'])
      iaca_tp = subprocess.check_output(iacaCMDLine + (['-analysis', 'THROUGHPUT'] if iacaVersion=='2.1' else []) + ['-no_interiteration', '/tmp/ramdisk/asm.o'], stderr=subprocess.STDOUT)
   except subprocess.CalledProcessError as e:
      print "Error: " + e.output
      return None

   if debugOutput:
      print instrNode.attrib['iform'] + ' - NoInteriteration'
      print iaca_tp

   htmlReports.append('<pre>' + iaca_tp + '</pre>\n')

   if not iaca_tp or ' !' in iaca_tp or ' X' in iaca_tp or ' 0X' in iaca_tp or not 'Total Num Of Uops' in iaca_tp:
      print "IACA error"
      return None

   cycles = float(iaca_tp.split('\n')[3].split()[2])
   return cycles

class TPConfig:
   def __init__(self, independentInstrs=None, depBreakingInstrs='', init=None, preInstrCode='', preInstrNodes=None, note=''):
      self.independentInstrs = ([] if independentInstrs is None else independentInstrs)
      self.depBreakingInstrs = depBreakingInstrs
      self.init = ([] if init is None else init)
      self.preInstrCode = preInstrCode
      self.preInstrNodes = ([] if preInstrNodes is None else preInstrNodes)
      self.note = note

def getTPConfigs(instrNode, useDistinctRegs=True, computeIndepAndDepBreakingInstrs=True):
   iform = instrNode.attrib['iform']
   iclass = instrNode.attrib['iclass']

   if 'DIV' in iclass or 'SQRT' in iclass:
      return getTPConfigsForDiv(instrNode)

   independentInstrs = []
   depBreakingInstrs = ''
   if computeIndepAndDepBreakingInstrs:
      independentInstrs = getIndependentInstructions(instrNode, useDistinctRegs)
      depBreakingInstrs = getDependencyBreakingInstrsForSuppressedOperands(instrNode)

   # instructions with multiple configs
   if 'I8' in instrNode.attrib['string']:
      configs = []
      for immediate in [0, 1, 2]:
         if instrNode.attrib['string'].replace('I8', str(immediate)) in instrNodeDict:
            continue
         config = TPConfig(note='With immediate = ' + str(immediate))
         config.independentInstrs = getIndependentInstructions(instrNode, useDistinctRegs, immediate=immediate)
         config.depBreakingInstrs = depBreakingInstrs
         configs.append(config)
      return configs

   if iclass in ['JB', 'JBE', 'JLE', 'JNB', 'JNBE', 'JNLE', 'JNO', 'JNP', 'JNS', 'JNZ', 'JO', 'JP', 'JS', 'JZ']:
      config0 = TPConfig(independentInstrs=independentInstrs, init=['pushfq; and qword ptr [RSP], ~0x8D5; popfq'], note='With all flags set to 0')
      config1 = TPConfig(independentInstrs=independentInstrs, init=['pushfq; or qword ptr [RSP], 0x8D5; popfq'], note='With all flags set to 1')
      return [config0, config1]

   if iclass in ['JL', 'JNL']:
      config0 = TPConfig(independentInstrs=independentInstrs, init=['pushfq; and qword ptr [RSP], ~0x8D5; popfq'], note='With SF=OF')
      config1 = TPConfig(independentInstrs=independentInstrs, init=['pushfq; and qword ptr [RSP], ~0x8D5; or qword ptr [RSP], 0x80; popfq'], note='With SF!=OF')
      return [config0, config1]

   if iclass in ['JRCXZ']:
      config0 = TPConfig(independentInstrs=independentInstrs, init=['mov RCX, 0'], note='With RCX=0')
      config1 = TPConfig(independentInstrs=independentInstrs, init=['mov RCX, 1'], note='With RCX=1')
      return [config0, config1]

   if 'LOOP' in iform or 'REP' in iform:
      configs = []
      for regVal in ['0', '1', '2']:
         config = TPConfig(independentInstrs=independentInstrs, preInstrCode='mov RCX, '+regVal, note='With RCX='+regVal)
         if instrNode.attrib['category'] in ['IOSTRINGOP']:
            config.init = ['mov DX, 0x80']
         configs.append(config)
      return configs

   # instructions with one config
   preInstrCode, preInstrNodes = getPreInstr(instrNode)
   config = TPConfig(independentInstrs, depBreakingInstrs, [], preInstrCode, preInstrNodes)

   if re.search('BT.*MEMv_GPRv', iform):
      config.init = list(set('mov ' + regTo64(r) + ', 0' for i in independentInstrs for r in i.readRegs if not regTo64(r) in globalDoNotWriteRegs))

   if iform in ['CALL_NEAR_GPRv', 'JMP_GPRv']:
      config.independentInstrs = [getInstrInstanceFromNode(instrNode, opRegDict={1: 'RAX'})]

   if iform in ['CALL_NEAR_MEMv', 'JMP_MEMv']:
      config.independentInstrs = [getInstrInstanceFromNode(instrNode)]

   if iclass == 'FXRSTOR': config.init = ['FXSAVE [R14]']
   if iclass == 'FXRSTOR64': config.init = ['FXSAVE64 [R14]']

   if iform in ['IN_AL_IMMb', 'IN_OeAX_IMMb', 'OUT_IMMb_AL', 'OUT_IMMb_OeAX']:
      config.independentInstrs = getIndependentInstructions(instrNode, useDistinctRegs, immediate=0x80)

   if iform in ['IN_AL_DX', 'IN_OeAX_DX', 'OUT_DX_AL', 'OUT_DX_OeAX'] or instrNode.attrib['category'] in ['IOSTRINGOP']:
      config.init = ['mov DX, 0x80']

   if iform == 'LLDT_GPR16': config.init = list(set('SLDT ' + reg for i in independentInstrs for reg in i.readRegs))
   if iform == 'LMSW_GPR16': config.init = list(set('SMSW ' + reg for i in independentInstrs for reg in i.readRegs))
   if iform == 'LMSW_MEMw': config.init = list(['SMSW [R14+'+str(i*64)+']' for i in range(0,maxTPRep)])

   if iform == 'POPF':
      config.init = ['PUSHF; POP AX']
   if iform == 'POPFQ':
      config.init = ['PUSHFQ; pop RAX']

   if iform in ['RDMSR', 'WRMSR']: config.init = ['MOV RCX, 0xE7'] #TSC Frequency Clock Counter
   if iform in ['RDPMC']: config.init = ['MOV RCX, 0']

   if iform == 'RET_NEAR_IMMw':
      config.independentInstrs = [getInstrInstanceFromNode(instrNode, immediate=8)]

   return [config]

def getPreInstr(instrNode):
   iform = instrNode.attrib['iform']
   preInstrCode = ''
   preInstrNodes = None

   if iform in ['CALL_NEAR_GPRv', 'JMP_GPRv']:
      preInstrCode = 'lea RAX, [RIP+2]'
      preInstrNodes = [instrNodeDict['LEA_RD (R64)']]

   if iform in ['CALL_NEAR_MEMv', 'JMP_MEMv']:
      preInstrCode = 'lea RAX, [RIP+6]; mov [R14], RAX'
      preInstrNodes = [instrNodeDict['LEA_RD (R64)'], instrNodeDict['MOV (M64, RAX)']]

   if iform == 'LEAVE':
      preInstrCode = 'lea RBP, [R14]'
      preInstrNodes = [instrNodeDict['LEA_B (R64)']]

   if iform == 'POPF':
      preInstrCode = 'PUSH AX'
      preInstrNodes = [instrNodeDict['PUSH (R16)']]

   if iform == 'POPFQ':
      preInstrCode = 'PUSH RAX'
      preInstrNodes = [instrNodeDict['PUSH (R64)']]

   if iform == 'RET_NEAR':
      preInstrCode = 'lea RAX, [RIP+5]; mov [RSP], RAX'
      preInstrNodes = [instrNodeDict['LEA_RD (R64)'], instrNodeDict['MOV (M64, RAX)']]

   if iform == 'RET_NEAR_IMMw':
      preInstrCode = 'lea RAX, [RIP+7]; mov [RSP], RAX'
      preInstrNodes = [instrNodeDict['LEA_RD (R64)'], instrNodeDict['MOV (M64, RAX)']]

   return (preInstrCode, preInstrNodes)

# Returns [minConfig, maxConfig]
def getTPConfigsForDiv(instrNode):
   memDivisor = len(instrNode.findall('./operand[@type="mem"]'))>0
   iclass = instrNode.attrib['iclass']

   minConfig = TPConfig(note='Fast division')
   maxConfig = TPConfig(note='Slow division')

   if iclass in ['DIV', 'IDIV']:
      for op in instrNode.iter('operand'):
         if op.attrib.get('suppressed', '0') == '0':
            memDivisor = op.attrib['type'] == 'mem'
            width = int(op.attrib['width'])
            if width == 8:
               maxConfig.preInstrCode = 'MOV AX, 13057'
               maxConfig.preInstrNodes = [instrNodeDict['MOV (R16, I16)']]
               maxDivisor = '123'
            elif width == 16:
               maxConfig.preInstrCode = 'MOV AX, 133; MOV DX, 0x343a'
               maxConfig.preInstrNodes = [instrNodeDict['MOV (R16, I16)'], instrNodeDict['MOV (R16, I16)']]
               maxDivisor = '0x75e6'
            elif width == 32:
               maxConfig.preInstrCode = 'MOV EAX, 133; MOV EDX, 0x343a9ed7'
               maxConfig.preInstrNodes = [instrNodeDict['MOV (R32, I32)'], instrNodeDict['MOV (R32, I32)']]
               maxDivisor = '0x75e6e44f'
            else:
               maxConfig.preInstrCode = 'MOV RAX, 133; MOV RDX, 0x343a9ed744556677'
               maxConfig.preInstrNodes = [instrNodeDict['MOV (R64, I32)'],instrNodeDict['MOV (R64, I64)']]
               maxDivisor = '0x75e6e44fccddeeff'

            if memDivisor:
               memPrefix = instrNode.findall('./operand[@type="mem"]')[0].attrib['memory-prefix']
               minConfig.init = ['MOV ' + memPrefix + ' [R14], 1']
               maxConfig.init = ['MOV ' + regToSize('R8', width) + ', ' + maxDivisor + '; MOV ' + memPrefix + ' [R14], ' + regToSize('R8', width)]
               instrI = getInstrInstanceFromNode(instrNode)
            else:
               minConfig.init = ['MOV ' + regToSize('RBX', width) + ', 1']
               maxConfig.init = ['MOV ' + regToSize('RBX', width) + ', ' + maxDivisor]
               instrI = getInstrInstanceFromNode(instrNode, opRegDict={int(op.attrib['idx']):regToSize('RBX', width)})

            minConfig.independentInstrs = [instrI]
            maxConfig.independentInstrs = [instrI]
            minConfig.init += ['MOV RAX, 0; MOV RDX, 0']
            minConfig.preInstrCode = 'MOV RAX, 0; MOV RDX, 0'
            minConfig.preInstrNodes = [instrNodeDict['MOV (R64, I32)'], instrNodeDict['MOV (R64, I32)']]
   elif iclass in ['DIVSS', 'DIVPS', 'DIVSD', 'DIVPD', 'VDIVSS', 'VDIVPS', 'VDIVSD', 'VDIVPD']:
      dataType = iclass[-1]
      if dataType == 'S':
         maxDividend = '0x54ed392654ed3926' #8.15093E12 in high and low 32-bit
         maxDivisor = '0x3f99f4c03f99f4c0' #1.20278 in high and low 32-bit
         minDividend = '0x3f8000003f800000' #1.0 in high and low 32-bit
         minDivisor = '0x3f8000003f800000' #1.0 in high and low 32-bit
      else:
         maxDividend = '0x429da724b687da66' #8.1509281715106E12
         maxDivisor = '0x3ff33e97f934078b' #1.20278165192619
         minDividend = '0x3ff0000000000000' #1.0
         minDivisor = '0x3ff0000000000000' #1.0

      for config, dividend, divisor in [(maxConfig, maxDividend, maxDivisor), (minConfig, minDividend, minDivisor)]:
         config.init = ['MOV RAX, ' + dividend]
         config.init += ['MOV RBX, ' + divisor]
         for i in range(0, 64, 8): config.init += ['MOV [R14+' + str(i) + '], RBX']
         for i in range(64, 128, 8): config.init += ['MOV [R14+' + str(i) + '], RAX']

         if instrNode.attrib['iclass'] in ['DIVSS', 'DIVPS', 'DIVSD', 'DIVPD']:
            config.init += ['MOVUP' + dataType + ' XMM0, [R14]']
            config.init += ['MOVUP' + dataType + ' XMM1, [R14+64]']
            config.init += ['MOVUP' + dataType + ' XMM2, XMM1']
            config.preInstrCode = 'MOVUP' + dataType + ' XMM2, XMM1; '
            config.preInstrNodes = [instrNodeDict['MOVUP' + dataType + '_0F10 (XMM, XMM)']]
            config.independentInstrs = [getInstrInstanceFromNode(instrNode, opRegDict={1:'XMM2', 2:'XMM0'})]
         else:
            regType = 'XMM'
            if 'YMM' in instrNode.attrib['iform']: regType = 'YMM'
            if 'ZMM' in instrNode.attrib['iform']: regType = 'ZMM'

            nOperands = len(instrNode.findall('./operand'))

            dividendReg = regType + '0'
            divisorReg = regType + '1'

            config.init += ['VMOVUP' + dataType + ' ' +  dividendReg + ', [R14+64]']
            config.init += ['VMOVUP' + dataType + ' ' +  divisorReg + ', [R14]']

            config.independentInstrs = [getInstrInstanceFromNode(instrNode,  opRegDict={1:regType+str(reg), (nOperands-1):dividendReg, nOperands:divisorReg}) for reg in range(2, 10)]
   elif instrNode.attrib['iclass'] in ['SQRTSS', 'SQRTPS', 'SQRTSD', 'SQRTPD', 'RSQRTSS', 'RSQRTPS', 'RCPSS', 'RCPPS', 'VSQRTSS', 'VSQRTPS', 'VSQRTSD', 'VSQRTPD','VRSQRTSS', 'VRSQRTPS', 'VRCPSS', 'VRCPPS', 'VRSQRT14SS', 'VRSQRT14SD', 'VRSQRT14PS', 'VRSQRT14PD']:
      dataType = instrNode.attrib['iclass'][-1]

      if dataType == 'S':
         maxArg = '0x72d30ff172d30ff1' #8.36104E30 in high and low 32-bit
         minArg = '0x3f8000003f800000' #1.0 in high and low 32-bit
      else:
         maxArg = '0x465a61fe1acdc21c' #8.3610378602352937E30
         minArg = '0x3ff0000000000000' #1.0

      instrPrefix = ''
      if instrNode.attrib['iclass'].startswith('V'): instrPrefix = 'V'

      for arg, config in [(maxArg, maxConfig), (minArg, minConfig)]:
         regType = 'XMM'
         if 'YMM' in instrNode.attrib['iform']: regType = 'YMM'
         if 'ZMM' in instrNode.attrib['iform']: regType = 'ZMM'

         targetRegIdx = min(int(opNode.attrib['idx']) for opNode in instrNode.findall('./operand') if opNode.text and regType in opNode.text)

         config.init = ['MOV RAX, ' + arg]

         if memDivisor:
            for i in range(0, 64, 8): config.init += ['MOV [R14+' + str(i) + '], RAX']
            instrs = [getInstrInstanceFromNode(instrNode, opRegDict={targetRegIdx:regType+str(reg)}) for reg in range(2, 10)]
         else:
            sourceReg = regType + '0'
            config.init += [instrPrefix + 'MOVUP' + dataType + ' ' +  sourceReg + ', [R14]']
            sourceRegIdx = max(int(opNode.attrib['idx']) for opNode in instrNode.findall('./operand') if opNode.text and regType in opNode.text)
            instrs = [getInstrInstanceFromNode(instrNode, opRegDict={targetRegIdx:regType+str(reg), sourceRegIdx: sourceReg}) for reg in range(2, 10)]

         config.independentInstrs = instrs

   return [minConfig, maxConfig]


# rounds to the nearest multiple of 1/5, 1/4, or 1/3 (in that order) if the value is at most 0.015 smaller or larger than this multiple;
# otherwise rounds to two decimals
def fancyRound(cycles):
   round5 = round(round(cycles*5)/5, 2)
   round4 = round(round(cycles*4)/4, 2)
   round3 = round(round(cycles*3)/3, 2)
   if abs(round5-cycles) <= 0.015:
      return round5
   elif abs(round4-cycles) <= 0.015:
      return round4
   elif abs(round3-cycles) <= 0.015:
      return round3
   return round(cycles, 2)


TPResult = namedtuple('TPResult', ['TP', 'TP_noDepBreaking_noLoop', 'TP_single', 'uops', 'fused_uops', 'divCycles', 'ILD_stalls', 'dec0', 'config', 'unblocked_ports'])

# returns TPResult
# port usages are averages (when no ports are blocked by other instructions)
def getThroughputAndUops(instrNode, useDistinctRegs, htmlReports):
   configs = getTPConfigs(instrNode, useDistinctRegs)

   minTP = sys.maxint
   minTP_noDepBreaking_noLoop = sys.maxint
   minTP_single = sys.maxint

   if useIACA:
      config = configs[0] # consider only first config as IACA does not seem to consider different values in registers

      instrList = [x.asm for x in config.independentInstrs]
      for ic in sorted(set([1, len(instrList)])):
         if len(instrList) > 1: htmlReports.append('<h3>With ' + str(ic) + ' independent instruction' + ('s' if ic>1 else '') + '</h3>\n')
         if ic > 1: htmlReports.append('<hr>\n')
         for useDepBreakingInstrs in [False, True]:
            if useDepBreakingInstrs:
               if not config.depBreakingInstrs: continue
               instrStr = ";".join([i+';'+config.depBreakingInstrs for i in instrList[0:ic]])
               htmlReports.append('<h4>With additional dependency-breaking instructions</h4>\n')
            else:
               instrStr = ";".join(instrList[0:ic])

            createIacaAsmFile("/tmp/ramdisk/asm.s", "", 0, instrStr)
            try:
               subprocess.check_output(['as', '/tmp/ramdisk/asm.s', '-o', '/tmp/ramdisk/asm.o'])
               iaca_out = subprocess.check_output(iacaCMDLine + ['/tmp/ramdisk/asm.o'], stderr=subprocess.STDOUT)
            except subprocess.CalledProcessError as e:
               logging.warn('Error: ' + e.output)
               if minTP != sys.maxint:
                  htmlReports.append('<pre>' + e.output + '</pre>\n')
                  continue # on SNB, IACA 2.2 crashes on only some (larger) inputs
               else:
                  return None

            if not iaca_out or ' ! ' in iaca_out or ' X ' in iaca_out or ' 0X ' in iaca_out or not 'Total Num Of Uops' in iaca_out:
               print "IACA error"
               return None

            print instrNode.attrib['iform'] + ' - throughput'

            htmlReports.append('<pre>' + iaca_out + '</pre>\n')

            cycles = float(iaca_out.split('\n')[3].split()[2])
            cycles = cycles/ic
            minTP = min(minTP, cycles)
            if not useDepBreakingInstrs: minTP_noDepBreaking_noLoop = min(minTP_noDepBreaking_noLoop, cycles)

            if ic == 1 and not useDepBreakingInstrs:
               minTP_single = min(minTP_single, cycles)

               unfused_uops_line = iaca_out.split('\n')[-2]
               unfused_uops = int(unfused_uops_line.split()[4])/ic

               ports_line = iaca_out.split('\n')[-3]
               fused_uops = '^' in ports_line.split()[1]

               num_ports = re.search('\|  Port  \|.*', iaca_out).group(0).count('|')-2

               ports_dict = {}
               for p in range(0, num_ports):
                  portCol = ports_line.split('|')[p+2].split()
                  if portCol:
                     ports_dict[p] = float(portCol[0])
                  else:
                     ports_dict[p] = 0.0

               port0 = ports_line.split('|')[2].split()
               if len(port0)>1:
                  divCycles = int(float(port0[1]))
               else:
                  divCycles = 0

      return TPResult(minTP, minTP_noDepBreaking_noLoop, minTP_single, unfused_uops, fused_uops, divCycles, 0, False, config, ports_dict)
   else:
      hasMemWriteOperand = len(instrNode.findall('./operand[@type="mem"][@r="1"][@w="1"]'))>0
      uops = 0
      uopsFused = 0
      divCycles = 0
      ILD_stalls = 0
      dec0 = False
      ports_dict = {}
      for config in configs:
         if config.note: htmlReports.append('<h2>' + config.note + '</h2>\n')

         instrIList = config.independentInstrs
         for ic in sorted(set([1, min(4, len(instrIList)), min(8, len(instrIList)), len(instrIList)])):
            if len(instrIList) > 1: htmlReports.append('<h3 style="margin-left: 25px">With ' + str(ic) + ' independent instruction' + ('s' if ic>1 else '') + '</h3>\n')
            htmlReports.append('<div style="margin-left: 50px">')

            for useDepBreakingInstrs in ([False, True] if config.depBreakingInstrs else [False]):
               if useDepBreakingInstrs:
                  instrStr = ';'.join([config.depBreakingInstrs+';'+config.preInstrCode+';'+i.asm for i in instrIList[0:ic]])
                  htmlReports.append('<h4>With additional dependency-breaking instructions</h4>\n')
               else:
                  instrStr = ';'.join([config.preInstrCode+';'+i.asm for i in instrIList[0:ic]])

               init = list(chain.from_iterable(i.regMemInit for i in instrIList[0:ic])) + config.init

               for repType in ['unrollOnly', 'loopSmall', 'loopBig']:
                  if minTP < sys.maxint and minTP > 100: continue

                  if repType == 'unrollOnly':
                     unrollCount = int(round(500/ic+49, -2)) # should still fit in the icache
                     if instrNode.attrib['iclass'] in ['WBINVD']: unrollCount /= 10;
                     loopCount = 0
                  else:
                     # we test with a small loop body so that uops may be delivered from the loop stream detector (LSD)
                     # we also test with a larger loop body to minimize potential overhead from the loop itself
                     loopCount = 100;
                     unrollCount = max(1, int(round(10.0/ic)))
                     if repType == 'loopBig':
                        unrollCount *= 10

                  if loopCount > 0:
                     htmlReports.append('<h4>With loop_count=' + str(loopCount) + ' and unroll_count=' + str(unrollCount) + '</h4>\n')
                  else:
                     htmlReports.append('<h4>With unroll_count=' + str(unrollCount) +' and no inner loop</h4>\n')

                  htmlReports.append('<ul>\n')
                  result = runExperiment(instrNode, instrStr, init=init, unrollCount=unrollCount, loopCount=loopCount, basicMode=(loopCount>0),
                                         htmlReports=htmlReports)
                  htmlReports.append('</ul>\n')

                  cycles = fancyRound(result['Core cycles']/ic)

                  invalid = False
                  if any('PORT' in e for e in result):
                     maxPortUops = max(v/(len(e)-9) for e,v in result.items() if e.startswith('UOPS_PORT'))
                     if maxPortUops * .98 > result['Core cycles']:
                        print 'More uops on ports than cycles, uops: {}, cycles: {}'.format(maxPortUops, result['Core cycles'])
                        invalid = True

                  if not invalid:
                     minTP = min(minTP, cycles)
                     if not useDepBreakingInstrs and repType == 'unrollOnly': minTP_noDepBreaking_noLoop = min(minTP_noDepBreaking_noLoop, cycles)

                  if ic == 1 and (minTP == sys.maxint or cycles == minTP) and not useDepBreakingInstrs and repType == 'unrollOnly':
                     minTP_single = min(minTP_single, cycles)

                     if isIntelCPU():
                        ports_dict = {int(p[9:]): i for p, i in result.items() if 'UOPS_PORT' in p}
                     elif isAMDCPU() and not instrNode.attrib['extension'] == 'BASE':
                        # We ignore BASE instructions, as they sometimes wrongly count floating point uops
                        ports_dict = {int(p[23:]): i for p, i in result.items() if 'FpuPipeAssignment.Total' in p}

                     uops = int(result['UOPS']+.1)
                     if 'RETIRE_SLOTS' in result:
                        uopsFused = int(result['RETIRE_SLOTS']+.1)

                     if 'ILD_STALL.LCP' in result:
                        ILD_stalls = int(result['ILD_STALL.LCP'])

                     if 'INST_DECODED.DEC0' in result:
                        dec0 = (int(round(result['INST_DECODED.DEC0'])) > 0)

                     if 'DIV_CYCLES' in result:
                        divCycles = int(result['DIV_CYCLES']+.1)

                     minConfig = config

            htmlReports.append('</div>')

      if minTP < sys.maxint:
         return TPResult(minTP, minTP_noDepBreaking_noLoop, minTP_single, uops, uopsFused, divCycles, ILD_stalls, dec0, minConfig, ports_dict)


basicLatency = {}

def getBasicLatencies(instrNodeList):
   movsxResult = runExperiment(instrNodeDict['MOVSXD (R64, R32)'], 'MOVSX RAX, EAX')
   movsxCycles = int(round(movsxResult['Core cycles']))
   if not movsxCycles == 1:
      print 'Latency of MOVSX must be 1'
      sys.exit()
   basicLatency['MOVSX'] = movsxCycles

   andResult = runExperiment(instrNodeDict['AND_21 (R64, R64)'], 'AND RAX, RBX')
   basicLatency['AND'] = int(round(andResult['Core cycles']))

   orResult = runExperiment(instrNodeDict['OR_09 (R64, R64)'], 'OR RAX, RBX')
   basicLatency['OR'] = int(round(orResult['Core cycles']))

   xorResult = runExperiment(instrNodeDict['XOR_31 (R64, R64)'], 'XOR RAX, RBX')
   basicLatency['XOR'] = int(round(xorResult['Core cycles']))

   cmcResult = runExperiment(instrNodeDict['CMC'], 'CMC')
   basicLatency['CMC'] = int(round(cmcResult['Core cycles']))

   movqResult = runExperiment(instrNodeDict['MOVQ_0F6F (MM, MM)'], 'MOVQ MM0, MM0')
   basicLatency['MOVQ'] = int(round(movqResult['Core cycles']))

   for flag in STATUSFLAGS_noAF:
      testSetResult = runExperiment(None, 'TEST AL, AL; SET' + flag[0] + ' AL')
      testSetCycles = int(round(testSetResult['Core cycles']))
      if not testSetCycles == 2:
         print 'Latencies of TEST and SET' + flag[0] + ' must be 1'
         sys.exit()
      basicLatency['SET' + flag[0]] = 1
      basicLatency['TEST'] = 1

      testCmovResult = runExperiment(None, 'TEST RAX, RAX; CMOV' + flag[0] + ' RAX, RAX')
      basicLatency['CMOV' + flag[0]] = int(round(testCmovResult['Core cycles'])) - 1

   for instr in ['ANDPS', 'ANDPD', 'ORPS', 'ORPD', 'PAND', 'POR']:
      result = runExperiment(instrNodeDict[instr + ' (XMM, XMM)'], instr + ' XMM1, XMM1')
      basicLatency[instr] = int(round(result['Core cycles']))

   for instr in ['PSHUFD', 'SHUFPD']:
      result = runExperiment(instrNodeDict[instr + ' (XMM, XMM, I8)'], instr + ' XMM1, XMM1, 0')
      basicLatency[instr] = int(round(result['Core cycles']))

   if filter(lambda x: x.findall('[@iclass="VANDPS"]'), instrNodeList):
      for instr in ['VANDPS', 'VANDPD', 'VORPS', 'VORPD', 'VPAND', 'VPOR']:
         result = runExperiment(instrNodeDict[instr + ' (XMM, XMM, XMM)'], instr + ' XMM1, XMM1, XMM1')
         basicLatency[instr] = int(round(result['Core cycles']))

      for instr in ['VSHUFPD']:
         result = runExperiment(instrNodeDict[instr + ' (XMM, XMM, XMM, I8)'], instr + ' XMM1, XMM1, XMM1, 0')
         basicLatency[instr] = int(round(result['Core cycles']))

      for instr in ['VPSHUFD']:
         result = runExperiment(instrNodeDict[instr + ' (XMM, XMM, I8)'], instr + ' XMM1, XMM1, 0')
         basicLatency[instr] = int(round(result['Core cycles']))

   if filter(lambda x: x.findall('[@extension="AVX512EVEX"]'), instrNodeList):
      kmovq_result = runExperiment(instrNodeDict['KMOVQ (K, K)'], 'KMOVQ K1, K1')
      basicLatency['KMOVQ'] = int(round(kmovq_result['Core cycles']))

      for regType in ['XMM', 'YMM', 'ZMM']:
         vmovups_result = runExperiment(instrNodeDict['VMOVUPS ({0}, K, {0})'.format(regType)], 'VMOVUPS ' + regType + '1 {k1}, ' + regType + '1')
         vmovups_cycles = int(round(vmovups_result['Core cycles']))
         vmovups_uops = int(round(vmovups_result['UOPS']))
         basicLatency['VMOVUPS_' + regType + '_' + 'K'] = vmovups_cycles

         if not vmovups_uops == 1:
            print 'VMOVUPS must have exactly 1 uop'
            sys.exit()

         vpmovq2m_result = runExperiment(instrNodeDict['VPMOVQ2M (K, ' + regType + ')'],
                                         'VPMOVQ2M K1, ' + regType + '1; VMOVUPS ' + regType + '1 {k1}, ' + regType + '1')
         basicLatency['VPMOVQ2M_'+regType] = int(round(vpmovq2m_result['Core cycles'])) - vmovups_cycles

         vptestnmq_result = runExperiment(instrNodeDict['VPTESTNMQ (K, K, {0}, {0})'.format(regType)],
                                          'VPTESTNMQ K1 {K1}, ' + regType + '1, ' + regType + '1; VMOVUPS ' + regType + '1 {k1}, ' + regType + '1')
         basicLatency['VPTESTNMQ_'+regType] = int(round(vptestnmq_result['Core cycles'])) - vmovups_cycles

   for memWidth in [8, 16, 32, 64]:
      reg = regToSize('R12', memWidth)
      mov_10movsx_mov_result = runExperiment(None, 'mov ' + reg + ', [r14];' + ';'.join(10*['MOVSX R12, R12w']) + '; mov [r14], ' + reg , unrollCount=100)
      basicLatency['MOV_10MOVSX_MOV_'+str(memWidth)] = int(round(mov_10movsx_mov_result['Core cycles']))

   print 'Basic Latencies: ' + str(basicLatency)

# Returns a dict {opNode: instr}, s.t. opNode is both read and written, and instr breaks the dependency
# Returns a list of dependency breaking instructions for operands that are both read and written (with the exception of ignoreOperand, if specified).
def getDependencyBreakingInstrs(instrNode, opRegDict, ignoreOperand = None):
   depBreakingInstrs = dict()
   for opNode in instrNode.findall('./operand[@type="reg"][@r="1"][@w="1"]'):
      if opNode == ignoreOperand: continue

      xtype = opNode.attrib.get('xtype', '')
      opI = int(opNode.attrib['idx'])
      if opI in opRegDict:
         reg = opRegDict[opI]
      elif opNode.attrib.get('suppressed', '0') == '1':
         reg = opNode.text
      regPrefix = re.sub('\d', '', reg)
      if reg in GPRRegs:
         if reg not in globalDoNotWriteRegs:
            depBreakingInstrs[opNode] = 'MOV ' + reg + ', 0' # don't use XOR as this would also break flag dependencies
         elif reg in ['RSP', 'RBP']:
            depBreakingInstrs[opNode] = 'MOV ' + reg + ', R14'
      elif xtype.startswith('f'):
         if isAVXInstr(instrNode):
            depBreakingInstrs[opNode] = 'VMOVUPD ' + reg + ', ' + regPrefix + '15'
         else:
            depBreakingInstrs[opNode] = 'MOVUPD ' + reg + ', ' + regPrefix + '15'
      elif regPrefix in ['XMM', 'YMM', 'ZMM'] and isAVXInstr(instrNode):
         depBreakingInstrs[opNode] = 'VXORPS ' + reg + ', ' + reg + ', ' + reg
      elif 'MM'in regPrefix:
         depBreakingInstrs[opNode] = 'PXOR ' + reg + ', ' + reg
   for opNode in instrNode.findall('./operand[@type="mem"][@r="1"][@w="1"]'):
      if opNode == ignoreOperand: continue

      memWidth = int(opNode.attrib['width'])
      if memWidth <= 64:
         depBreakingInstrs[opNode] = 'MOV ' + opNode.attrib['memory-prefix'] + ' [' + getAddrReg(instrNode, opNode) + '], 0'
      else:
         depBreakingInstrs[opNode] = 'MOVUPS [' + getAddrReg(instrNode, opNode) + '], XMM15'
   for opNode in instrNode.findall('./operand[@type="flags"][@w="1"]'):
      if opNode == ignoreOperand: continue
      if not (opNode.attrib.get('r', '') == '1' or opNode.attrib.get('conditionalWrite', '') == '1'): continue

      if not any(('flag_'+f in opNode.attrib) for f in STATUSFLAGS_noAF): continue
      depBreakingInstrs[opNode] = 'TEST R13, R13'

   return depBreakingInstrs


# Returns an assembler code string of dependency breaking instructions for suppressed operands (and operands with only one possible register) of instrNode.
def getDependencyBreakingInstrsForSuppressedOperands(instrNode):
   if instrNode.attrib['iclass'] in ['LEAVE']: return ''
   if instrNode.attrib['iclass'] in ['XLAT']: return 'XOR RAX, RAX'

   depBreakingInstrs = []
   xorInDepBreakingInstrs = False
   for opNode in instrNode.findall('./operand[@type="reg"][@r="1"]'):
      if opNode.attrib.get('suppressed', '0') == '0' and ',' in opNode.text: continue

      reg = opNode.text
      if not reg in GPRRegs: continue
      if reg in globalDoNotWriteRegs|specialRegs: continue

      writeOfRegFound = False
      for opNode2 in instrNode.findall('./operand[@type="reg"][@w="1"]'):
         if opNode2.attrib.get('suppressed', '0') == '0' and ',' in opNode2.text: continue
         reg2 = opNode2.text
         if regTo64(reg) == regTo64(reg2):
            writeOfRegFound = True
            break

      if writeOfRegFound:
         # we use the corresponding 64-bit register, as dependency breaking doesn't seem to work for reg sizes <= 16
         depBreakingInstrs += ['XOR ' + regTo64(reg) + ', ' + regTo64(reg)]
         xorInDepBreakingInstrs = True
   for opNode in instrNode.findall('./operand[@type="mem"][@r="1"][@w="1"][@suppressed="1"]'):
      depBreakingInstrs += ['MOV qword ptr [' + opNode.attrib['base'] + '], 0']
   if not xorInDepBreakingInstrs:
      for opNode in instrNode.findall('./operand[@type="flags"][@w="1"]'):
         # on some CPUs, instructions that write flags conditionally also read the flags
         if not (opNode.attrib.get('r', '') == '1' or opNode.attrib.get('conditionalWrite', '') == '1'): continue
         if not any(('flag_'+f in opNode.attrib) for f in STATUSFLAGS_noAF): continue
         depBreakingInstrs += ['TEST R13, R13']

   return ';'.join(depBreakingInstrs)


#constants are from Agner Fog's scripts
def getDivLatConfigLists(instrNode, opNode1, opNode2, cRep):
   if instrNode.attrib['iclass'] in ['DIV', 'IDIV']:
      for op in instrNode.iter('operand'):
         if op.attrib.get('suppressed', '0') == '0':
            divisorNode = op
      memDivisor = divisorNode.attrib['type'] == 'mem'
      width = int(divisorNode.attrib['width'])

      if memDivisor:
         instrI = getInstrInstanceFromNode(instrNode)
      else:
         divisorReg = regToSize('RBX', width)
         instrI = getInstrInstanceFromNode(instrNode, opRegDict={int(divisorNode.attrib['idx']):divisorReg})

      if width == 8:
         maxRAX = '13057'
         maxRDX = '0'
         maxDivisor = '123'
      elif width == 16:
         maxRAX = '133'
         maxRDX = '0x343a'
         maxDivisor = '0x75e6'
      elif width == 32:
         maxRAX = '133'
         maxRDX = '0x343a9ed7'
         maxDivisor = '0x75e6e44f'
      elif width == 64:
         maxRAX = '133'
         maxRDX = '0x343a9ed744556677'
         maxDivisor = '0x75e6e44fccddeeff'

      minRAX = '0'
      minRDX = '0'
      minDivisor = '1'

      configLists = []
      for RAX, RDX, divisor in [(minRAX, minRDX, minDivisor), (maxRAX, maxRDX, maxDivisor)]:
         configList = LatConfigList()
         configLists.append(configList)

         config = LatConfig(instrI)

         if RAX == maxRAX:
            config.notes.append('slow division')
         else:
            config.notes.append('fast division')

         immReg = {'RAX': 'R8', 'RDX': 'R9', 'divisor': 'R10'}
         config.init = ['MOV ' + immReg['RAX'] + ', ' + RAX,
                        'MOV ' + immReg['RDX'] + ', ' + RDX,
                        'MOV ' + immReg['divisor'] + ', ' + divisor]

         if memDivisor:
            config.init += ['MOV [R14], ' + immReg['divisor']]
         else:
            config.init += ['MOV RBX, ' + immReg['divisor']]
         config.init += ['MOV RAX, ' + immReg['RAX'],
                         'MOV RDX, ' + immReg['RDX']]

         chainInstrs = ''
         chainLatency = 0
         immInstr = ''
         for opNode in instrNode.iter('operand'):
            if opNode.attrib['type'] == 'flags': continue
            if opNode == opNode1:
               if opNode == divisorNode:
                  if memDivisor:
                     chainInstrs = 'MOVSX R12, ' + regToSize(opNode2.text, min(getRegSize(opNode2.text), 32)) + '; '
                     chainInstrs += ('XOR R14, R12; ') * cRep # cRep is a power of two
                     chainLatency = basicLatency['MOVSX'] + basicLatency['XOR'] * cRep
                  else:
                     chainInstrs = 'AND {0}, {1}; AND {0}, {2}; OR {0}, {2}; '.format(divisorReg, regToSize(opNode2.text, getRegSize(divisorReg)),
                                                                                     regToSize(immReg['divisor'], getRegSize(divisorReg)))
                     chainInstrs += 'OR {0}, {0}; '.format(divisorReg) * cRep
                     chainLatency = basicLatency['AND'] * 2 + basicLatency['OR'] * (cRep+1)
               else:
                  chainInstrs = 'AND {0}, {1}; OR {0}, {1}; '.format(opNode.text, regToSize(immReg[regTo64(opNode.text)], getRegSize(opNode.text)))
                  chainInstrs += 'OR {0}, {0}; '.format(opNode.text) * cRep
                  chainLatency = basicLatency['AND'] + basicLatency['OR'] * (cRep+1)

                  if opNode != opNode2:
                     chainInstrs = 'AND ' + opNode.text + ', ' + opNode2.text + '; ' + chainInstrs
                     chainLatency += basicLatency['AND']

            elif opNode != divisorNode:
               immInstr += 'MOV ' + opNode.text + ', ' + regToSize(immReg[regTo64(opNode.text)], getRegSize(opNode.text)) + ';'

         config.chainInstrs = chainInstrs + '; ' + immInstr
         config.chainLatency = chainLatency
         configList.append(config)
      return configLists
   elif instrNode.attrib['iclass'] in ['DIVSS', 'DIVPS', 'DIVSD', 'DIVPD', 'VDIVSS', 'VDIVPS', 'VDIVSD', 'VDIVPD']:
      memDivisor = len(instrNode.findall('./operand[@type="mem"]'))>0
      dataType = instrNode.attrib['iclass'][-1]

      if dataType == 'S':
         maxDividend = '0x54ed392654ed3926' # 8.15093E12 in high and low 32-bit
         maxDivisor = '0x3f99f4c03f99f4c0' # 1.20278 in high and low 32-bit
         minDividend = '0x3f8000003f800000' # 1.0 in high and low 32-bit
         minDivisor = '0x3f8000003f800000' # 1.0 in high and low 32-bit
      else:
         maxDividend = '0x429da724b687da66' # 8.1509281715106E12
         maxDivisor = '0x3ff33e97f934078b' # 1.20278165192619
         minDividend = '0x3ff0000000000000' # 1.0
         minDivisor = '0x3ff0000000000000' # 1.0

      configLists = []
      for dividend, divisor in [(maxDividend, maxDivisor), (minDividend, minDivisor)]:
         configList = LatConfigList()
         configLists.append(configList)

         regType = 'XMM'
         if 'YMM' in instrNode.attrib['iform']: regType = 'YMM'
         if 'ZMM' in instrNode.attrib['iform']: regType = 'ZMM'

         init = ['MOV RAX, ' + dividend]
         init += ['MOV RBX, ' + divisor]
         for i in range(0, getRegSize(regType)/8, 8): init += ['MOV [R14+' + str(i) + '], RBX']
         for i in range(64, 64+getRegSize(regType)/8, 8): init += ['MOV [R14+' + str(i) + '], RAX']

         if instrNode.attrib['iclass'] in ['DIVSS', 'DIVPS', 'DIVSD', 'DIVPD']:
            init += ['MOVUP' + dataType + ' XMM1, [R14+64]']
            init += ['MOVUP' + dataType + ' XMM2, [R14]']
            init += ['MOVUP' + dataType + ' XMM3, [R14+64]']
            init += ['MOVUP' + dataType + ' XMM4, [R14]']

            instrI = getInstrInstanceFromNode(instrNode, opRegDict={1:'XMM3', 2:'XMM4'})

            if opNode1 == opNode2:
               if dividend == minDividend:
                  # some CPUs seem to have some bypass delay when using (V)ORP*; we additionally test them with the same reg for both operands
                  instrISameReg = getInstrInstanceFromNode(instrNode, opRegDict={1:'XMM3', 2:'XMM3'})
                  config = LatConfig(instrISameReg, init=init)
                  configList.append(config)

               config = LatConfig(instrI, init=init)
               if dividend == maxDividend:
                  config.chainInstrs = 'ORP{0} XMM3, XMM1; ANDP{0} XMM3, XMM1; '.format(dataType)
                  config.chainLatency = basicLatency['ORP' + dataType] + basicLatency['ANDP' + dataType]
               config.chainInstrs += 'ORP{} XMM3, XMM3;'.format(dataType) * cRep
               config.chainLatency += basicLatency['ORP' + dataType] * cRep
               configList.append(config)
               configList.isUpperBound = True
            else:
               if memDivisor:
                  configList.isUpperBound = True
                  # find all other instrs from XMM3 to R12
                  for chainInstrI in getAllChainInstrsFromRegToReg(instrNode, 'XMM3', 'R12'):
                     if dividend == maxDividend:
                        chainInstrs = chainInstrI.asm + '; MOVUP'  + dataType + ' XMM3, XMM1; '
                     else:
                        chainInstrs = chainInstrI.asm + '; '
                     chainInstrs += ('XOR R14, R12; ') * cRep
                     chainLatency = 1 + basicLatency['XOR'] * cRep
                     configList.append(LatConfig(instrI, init=init, chainInstrs=chainInstrs, chainLatency=chainLatency))
               else:
                  if dividend == minDividend:
                     # some CPUs seem to have some bypass delay when using (V)ORP*; we additionally test them with the same reg for both operands
                     instrISameReg = getInstrInstanceFromNode(instrNode, opRegDict={1:'XMM3', 2:'XMM3'})
                     config = LatConfig(instrISameReg, init=init)
                     configList.append(config)

                  config = LatConfig(instrI, init=init)
                  if dividend == maxDividend:
                     config.chainInstrs = 'ANDP{0} XMM4, XMM3; MOVUP{0} XMM3, XMM1; ANDP{0} XMM4, XMM2; ORP{0} XMM4, XMM2; '.format(dataType)
                  else:
                     config.chainInstrs = 'ANDP{0} XMM4, XMM3; ANDP{0} XMM4, XMM2; ORP{0} XMM4, XMM2; '.format(dataType)
                  config.chainInstrs += 'ORP{} XMM4, XMM4; '.format(dataType) * cRep
                  config.chainLatency = basicLatency['ANDP' + dataType] * 2 + basicLatency['ORP' + dataType] * (cRep+1)
                  configList.append(config)
                  configList.isUpperBound = True
         else: # instrNode.attrib['iclass'] in ['VDIVSS', 'VDIVPS', 'VDIVSD', 'VDIVPD']:
            nOperands = len(instrNode.findall('./operand'))

            targetReg = regType + '0'
            dividendBaseReg = regType + '1'
            dividendReg = regType + '2'
            divisorBaseReg = regType + '3'
            divisorReg = regType + '4'

            init += ['VMOVUP' + dataType + ' ' +  dividendBaseReg + ', [R14+64]']
            init += ['VMOVUP' + dataType + ' ' +  dividendReg + ', [R14+64]']
            init += ['VMOVUP' + dataType + ' ' +  divisorBaseReg + ', [R14]']
            init += ['VMOVUP' + dataType + ' ' +  divisorReg + ', [R14]']

            instrI = getInstrInstanceFromNode(instrNode, opRegDict={1:targetReg, (nOperands-1):dividendReg, nOperands:divisorReg})

            if int(opNode1.attrib['idx']) == nOperands - 1: #dividend
               if dividend == minDividend:
                  # some CPUs seem to have some bypass delay when using (V)ORP*; we additionally test them with the same reg for both operands
                  instrISameReg = getInstrInstanceFromNode(instrNode, opRegDict={1:dividendReg, (nOperands-1):dividendReg, nOperands:divisorReg})
                  config = LatConfig(instrISameReg, init=init)
                  configList.append(config)

               config = LatConfig(instrI, init=init)
               config.chainInstrs = 'VORP{0} {1}, {2}, {2}; VORP{0} {1}, {1}, {3}; VANDP{0} {1}, {1}, {3}; '.format(dataType, dividendReg, targetReg, dividendBaseReg)
               config.chainInstrs += 'VORP{0} {1}, {1}, {1}; '.format(dataType, dividendReg) * cRep
               config.chainLatency = basicLatency['VORP' + dataType] * (cRep+2) + basicLatency['VANDP' + dataType]
               configList.append(config)
               configList.isUpperBound = True
            else: # divisor
               if memDivisor:
                  configList.isUpperBound = True
                  # find all other instrs from targetReg to R12
                  for chainInstrI in getAllChainInstrsFromRegToReg(instrNode, targetReg, 'R12'):
                     chainInstrs = chainInstrI.asm + '; ' + ('XOR R14, R12; ') * cRep
                     chainLatency = 1 + basicLatency['XOR'] * cRep
                     configList.append(LatConfig(instrI, init=init, chainInstrs=chainInstrs, chainLatency=chainLatency))
               else:
                  if divisor == minDivisor:
                     # some CPUs seem to have some bypass delay when using (V)ORP*; we additionally test them with the same reg for both operands
                     instrISameReg = getInstrInstanceFromNode(instrNode, opRegDict={1:divisorReg, (nOperands-1):dividendReg, nOperands:divisorReg})
                     config = LatConfig(instrISameReg, init=init)
                     configList.append(config)

                  config = LatConfig(instrI, init=init)
                  config.chainInstrs = 'VORP{0} {1}, {2}, {2}; VORP{0} {1}, {1}, {3}; VANDP{0} {1}, {1}, {3}; '.format(dataType, divisorReg, targetReg, divisorBaseReg)
                  config.chainInstrs += 'VORP{0} {1}, {1}, {1}; '.format(dataType, divisorReg) * cRep
                  config.chainLatency = basicLatency['VORP' + dataType] * (cRep+2) + basicLatency['VANDP' + dataType]
                  configList.append(config)
                  configList.isUpperBound = True
      return configLists
   elif instrNode.attrib['iclass'] in ['SQRTSS', 'SQRTPS', 'SQRTSD', 'SQRTPD', 'RSQRTSS', 'RSQRTPS', 'RCPSS', 'RCPPS', 'VSQRTSS', 'VSQRTPS', 'VSQRTSD',
                                       'VSQRTPD','VRSQRTSS', 'VRSQRTPS', 'VRSQRT14PD', 'VRSQRT14PS', 'VRSQRT14SD', 'VRSQRT14SS', 'VRCPSS', 'VRCPPS']:
      dataType = instrNode.attrib['iclass'][-1]

      if dataType == 'S':
         maxArg = '0x72d30ff172d30ff1' # 8.36104E30 in high and low 32-bit
         minArg = '0x3f8000003f800000' # 1.0 in high and low 32-bit
      else:
         maxArg = '0x465a61fe1acdc21c' # 8.3610378602352937E30
         minArg = '0x3ff0000000000000' # 1.0

      instrPrefix = ''
      if instrNode.attrib['iclass'].startswith('V'): instrPrefix = 'V'

      configLists = []

      for arg in [maxArg, minArg]:
         configList = LatConfigList()
         configLists.append(configList)

         regType = 'XMM'
         if 'YMM' in instrNode.attrib['iform']: regType = 'YMM'
         if 'ZMM' in instrNode.attrib['iform']: regType = 'ZMM'

         init = ['MOV RAX, ' + arg]
         for i in range(0, getRegSize(regType)/8, 8): init += ['MOV [R14+' + str(i) + '], RAX']

         targetReg = regType + '0'
         sourceBaseReg = regType + '1'
         sourceReg = regType + '2'

         init += [instrPrefix + 'MOVUP' + dataType + ' ' +  sourceReg + ', [R14]']
         init += [instrPrefix + 'MOVUP' + dataType + ' ' +  sourceBaseReg + ', [R14]']

         instrI = getInstrInstanceFromNode(instrNode, opRegDict={int(opNode2.attrib['idx']):targetReg, int(opNode1.attrib['idx']): sourceReg})

         if opNode1.attrib['type'] == 'mem':
            configList.isUpperBound = True
            # find all other instrs from targetReg to R12
            for chainInstrI in getAllChainInstrsFromRegToReg(instrNode, targetReg, 'R12'):
               chainInstrs = chainInstrI.asm + '; ' + ('XOR R14, R12; ') * cRep
               chainLatency = 1 + basicLatency['XOR'] * cRep
               configList.append(LatConfig(instrI, init=init, chainInstrs=chainInstrs, chainLatency=chainLatency))
         else:
            if arg == minArg:
               # some CPUs seem to have some bypass delay when using (V)ORP*; we additionally test them with the same reg for both operands
               instrISameReg = getInstrInstanceFromNode(instrNode, opRegDict={int(opNode2.attrib['idx']):sourceReg, int(opNode1.attrib['idx']): sourceReg})
               config = LatConfig(instrISameReg, init=init)
               configList.append(config)

            config = LatConfig(instrI, init=init)
            if instrPrefix == 'V':
               config.chainInstrs = 'VORP{0} {1}, {2}, {2}; VORP{0} {1}, {1}, {3}; VANDP{0} {1}, {1}, {3}; '.format(dataType, sourceReg, targetReg, sourceBaseReg)
               config.chainInstrs += 'VORP{0} {1}, {1}, {1}; '.format(dataType, sourceReg) * cRep
               config.chainLatency = basicLatency['VORP' + dataType] * (cRep+2) + basicLatency['VANDP' + dataType]
            else:
               config.chainInstrs = 'ORP{0} {1}, {2}; ORP{0} {1}, {3}; ANDP{0} {1}, {3}; '.format(dataType, sourceReg, targetReg, sourceBaseReg)
               config.chainInstrs += 'ORP{0} {1}, {1}; '.format(dataType, sourceReg) * cRep
               config.chainLatency = basicLatency['ORP' + dataType] * (cRep+2) + basicLatency['ANDP' + dataType]
            configList.append(config)
            configList.isUpperBound = True
      return configLists

# finds chain instructions from startReg to targetReg (including cases where only part of a reg is read/written)
def getAllChainInstrsFromRegToReg(instrNode, startReg, targetReg):
   allFPDataTypes = ['PD', 'PS', 'SD', 'SS']
   dataType = instrNode.attrib['iclass'][-2:]
   if dataType not in allFPDataTypes:
      dataType = ''

   result = []
   for chainInstrNode in instrNodeList:
      if instrNode.attrib.get('vex', '0') != chainInstrNode.attrib.get('vex', '0'): continue
      if instrNode.attrib.get('evex', '0') != chainInstrNode.attrib.get('evex', '0'): continue

      iclass = chainInstrNode.attrib['iclass']
      if dataType and any((d in iclass) for d in allFPDataTypes) and not dataType in iclass: continue

      for chainOpNode1 in chainInstrNode.findall('./operand[@type="reg"][@r="1"]'):
         regs1 = [r for r in chainOpNode1.text.split(',') if (r in GPRRegs and startReg in GPRRegs and regTo64(startReg)==regTo64(r)) or
                                                             ((r not in GPRRegs) and startReg[1:] == r[1:] and getRegSize(r) <= getRegSize(startReg))]
         if not regs1: continue
         reg1 = regs1[0]
         for chainOpNode2 in chainInstrNode.findall('./operand[@type="reg"][@w="1"]'):
            regs2 = [r for r in chainOpNode2.text.split(',') if r!=reg1 and ((r in GPRRegs and targetReg in GPRRegs and regTo64(targetReg)==regTo64(r)) or
                                                               ((r not in GPRRegs) and targetReg[1:] == r[1:] and getRegSize(r) <= getRegSize(targetReg)))]
            if not regs2: continue
            reg2 = regs2[0]
            result.append(getInstrInstanceFromNode(chainInstrNode, [reg1, reg2], [reg1, reg2], True, {int(chainOpNode1.attrib['idx']):reg1, int(chainOpNode2.attrib['idx']):reg2}))
   return result


def getLatConfigsFromMemToReg(instrNode, instrI, memOpNode, targetReg, addrReg, cRep):
   result = []

   if targetReg.startswith('MM'):
      result.append(LatConfig(instrI, chainInstrs='MOVQ ' + targetReg + ', [' + addrReg + '];', chainLatency=1))
   elif 'MM' in targetReg:
      memWidth = int(memOpNode.attrib['width'])

      if memWidth == 32:
         chainInstrFP = 'MOVSS'
         chainInstrInt = 'MOVD'
      elif memWidth == 64:
         chainInstrFP = 'MOVSD'
         chainInstrInt = 'MOVQ'
      else:
         chainInstrFP = 'MOVUPD'
         chainInstrInt = 'MOVDQU'

      if isAVXInstr(instrNode):
         chainInstrFP = 'V' + chainInstrFP
         chainInstrInt = 'V' + chainInstrInt

      chainInstrFP = chainInstrFP + ' XMM13, [' + addrReg + '];'
      fillInstrFP, fillLatFP = getChainInstrForVectorRegs(instrNode, 'XMM13', 'XMM' + targetReg[3:], cRep, 'FP')
      result.append(LatConfig(instrI, chainInstrs=chainInstrFP+fillInstrFP, chainLatency=1+fillLatFP))

      if not (targetReg[0:3] == 'YMM' and instrNode.attrib['extension'] == 'AVX'): # integers in YMM registers are only supported by AVX>=2
         chainInstrInt = chainInstrInt + ' XMM13, [' + addrReg + '];'
         fillInstrInt, fillLatInt = getChainInstrForVectorRegs(instrNode, 'XMM13', 'XMM' + targetReg[3:], cRep, 'Int')
         result.append(LatConfig(instrI, chainInstrs=chainInstrInt+fillInstrInt, chainLatency=1+fillLatInt))
   else:
      for chainInstrNode in instrNodeList:
         if instrNode.attrib.get('vex', '0') != chainInstrNode.attrib.get('vex', '0'): continue
         if instrNode.attrib.get('evex', '0') != chainInstrNode.attrib.get('evex', '0'): continue

         for chainOpNode1 in chainInstrNode.findall('./operand[@type="mem"][@r="1"]'):
            if chainOpNode1.attrib.get('suppressed', '0') == '1': continue
            if memOpNode.attrib['width'] != chainOpNode1.attrib['width']: continue
            if memOpNode.attrib.get('VSIB', '') != chainOpNode1.attrib.get('VSIB', ''): continue

            for chainOpNode2 in filter(lambda x: targetReg in x.text.split(','), chainInstrNode.findall('./operand[@type="reg"][@w="1"]')):
               if chainOpNode2.attrib.get('optional', '') == '1': continue
               chainsInstr = getInstrInstanceFromNode(chainInstrNode, [targetReg], [targetReg], True, {int(chainOpNode2.attrib['idx']):targetReg}).asm
               result.append(LatConfig(instrI, chainInstrs=chainsInstr, chainLatency=1))
   return result

def getLatConfigsFromRegToMem(instrNode, instrI, reg, addrReg, memWidth, cRep):
   result = []

   if reg.startswith('MM'):
      result.append(LatConfig(instrI, chainInstrs='MOVQ [' + addrReg + '], ' + reg + ';', chainLatency=1))
   elif 'MM' in reg:
      if memWidth <= 32:
         chainInstrFP = 'MOVSS'
         chainInstrInt = 'MOVD'
         regPrefix = 'XMM'
      elif memWidth == 64:
         chainInstrFP = 'MOVSD'
         chainInstrInt = 'MOVQ'
         regPrefix = 'XMM'
      elif memWidth == 128:
         chainInstrFP = 'MOVUPD'
         chainInstrInt = 'MOVDQU'
         regPrefix = 'XMM'
      elif memWidth == 256:
         chainInstrFP = 'MOVUPD'
         chainInstrInt = 'MOVDQU'
         regPrefix = 'YMM'
      elif memWidth == 512:
         chainInstrFP = 'MOVUPD'
         chainInstrInt = 'MOVDQU64'
         regPrefix = 'ZMM'

      if isAVXInstr(instrNode):
         chainInstrFP = 'V' + chainInstrFP
         chainInstrInt = 'V' + chainInstrInt

      reg1 = regPrefix + reg[3:]
      reg2 = regPrefix + '13'

      chainInstrFP = chainInstrFP + ' [' + addrReg + '], ' + reg2 + ';'
      fillInstrFP, fillLatFP = getChainInstrForVectorRegs(instrNode, reg1, reg2, cRep, 'FP')
      result.append(LatConfig(instrI, chainInstrs=fillInstrFP+chainInstrFP, chainLatency=1+fillLatFP))

      if not (regPrefix == 'YMM' and instrNode.attrib['extension'] == 'AVX'): # integers in YMM registers are only supported by AVX>=2
         chainInstrInt = chainInstrInt + ' [' + addrReg + '], ' + reg2 + ';'
         fillInstrInt, fillLatInt = getChainInstrForVectorRegs(instrNode, reg1, reg2, cRep, 'Int')
         result.append(LatConfig(instrI, chainInstrs=fillInstrInt+chainInstrInt, chainLatency=1+fillLatInt))
   else:
      # ToDo
      pass
   return result

def getChainInstrForVectorRegs(instrNode, startReg, targetReg, cRep, cType):
   # We use (V)SHUFPD instead of (V)MOV*PD because the latter is a 0-latency operation on some CPUs in some cases
   if cType == 'FP':
      if isAVXInstr(instrNode):
         if arch in ['ZEN+', 'ZEN2']:
            # on ZEN, all shuffles are integer operations
            chainInstrFP = 'VANDPD {0}, {1}, {1};'.format(targetReg, startReg)
            chainInstrFP += 'VANDPD {0}, {0}, {0};'.format(targetReg) * cRep
            chainLatencyFP = basicLatency['VANDPD'] * (cRep+1)
         else:
            chainInstrFP = 'VSHUFPD {0}, {1}, {1}, 0;'.format(targetReg, startReg)
            chainInstrFP += 'VSHUFPD {0}, {0}, {0}, 0;'.format(targetReg) * cRep
            chainLatencyFP = basicLatency['VSHUFPD'] * (cRep+1)
      else:
         if arch in ['ZEN+', 'ZEN2']:
            # on ZEN, all shuffles are integer operations
            chainInstrFP = 'VANDPD {0}, {1}, {1};'.format(targetReg, startReg)
            chainInstrFP += 'VANDPD {0}, {0}, {0};'.format(targetReg) * cRep
            chainLatencyFP = basicLatency['VANDPD'] * (cRep+1)
         else:
            chainInstrFP = 'SHUFPD {}, {}, 0;'.format(targetReg, startReg)
            chainInstrFP += 'SHUFPD {0}, {0}, 0;'.format(targetReg) * cRep
            chainLatencyFP = basicLatency['SHUFPD'] * (cRep+1)
      return (chainInstrFP, chainLatencyFP)
   else:
      if isAVXInstr(instrNode):
         chainInstrInt = 'VPSHUFD {}, {}, 0;'.format(targetReg, startReg)
         chainInstrInt += 'VPSHUFD {0}, {0}, 0;'.format(targetReg) * cRep
         chainLatencyInt = basicLatency['VPSHUFD'] * (cRep+1)
      else:
         chainInstrInt = 'PSHUFD {}, {}, 0;'.format(targetReg, startReg)
         chainInstrInt += 'PSHUFD {0}, {0}, 0;'.format(targetReg) * cRep
         chainLatencyInt = basicLatency['PSHUFD'] * (cRep+1)
      return (chainInstrInt, chainLatencyInt)


class LatConfig:
   def __init__(self, instrI, chainInstrs='', chainLatency=0, init=None, notes=None):
      self.instrI = instrI
      self.chainInstrs = chainInstrs
      self.chainLatency = chainLatency
      self.init = ([] if init is None else init)
      self.notes = ([] if notes is None else notes)

class LatConfigList:
   def __init__(self, latConfigs=None, sameReg = False, isUpperBound=False, notes=None):
      self.latConfigs = ([] if latConfigs is None else latConfigs)
      self.isUpperBound = isUpperBound
      self.notes = ([] if notes is None else notes)

   def append(self, latConfig):
      self.latConfigs.append(latConfig)

   def extend(self, latConfigs):
      self.latConfigs.extend(latConfigs)

LatResult = namedtuple('LatResult', ['minLat','maxLat','lat_sameReg','isUpperBound'])

def getLatConfigLists(instrNode, startNode, targetNode, useDistinctRegs, addr_mem, tpDict):
   cRep = min(100, 2 + 2 * int(math.ceil(tpDict[instrNode].TP_single / 2))) # must be a multiple of 2

   if 'DIV' in instrNode.attrib['iclass'] or 'SQRT' in instrNode.attrib['iclass']:
      if not useDistinctRegs: return None
      if targetNode.attrib['type'] == 'flags': return None
      if addr_mem == 'mem': return None
      if startNode.attrib.get('opmask', '') == '1' or targetNode.attrib.get('opmask', '') == '1': return None
      if instrNode.attrib.get('mask', '') == '1' and (startNode == targetNode): return None
      return getDivLatConfigLists(instrNode, startNode, targetNode, cRep)

   init = []

   startNodeIdx = int(startNode.attrib['idx'])
   targetNodeIdx = int(targetNode.attrib['idx'])

   suppressedStart = startNode.attrib.get('suppressed', '0') == '1'
   suppressedTarget = targetNode.attrib.get('suppressed', '0') == '1'

   instrReadsFlags = len(instrNode.findall('./operand[@type="flags"][@r="1"]')) > 0

   configList = LatConfigList()

   if startNode.text == 'RSP' or targetNode.text == 'RSP':
      # we ignore operands that modify the stack pointer, as these are usually handled by the stack engine in the issue stage of the pipeline, and
      # thus would not lead to meaningful results
      return None
   elif (startNode.text and 'RIP' in startNode.text) or (targetNode.text and 'RIP' in targetNode.text):
      return None
   elif startNode.attrib['type'] == 'reg':
      #################
      # reg -> ...
      #################
      regs1 = set(startNode.text.split(","))-globalDoNotWriteRegs-specialRegs

      if not regs1: return None

      if targetNode.attrib['type'] == 'reg':
         #################
         # reg -> reg
         #################
         regs2 = set(targetNode.text.split(","))-globalDoNotWriteRegs-specialRegs

         if not regs2:
            return None

         if startNode == targetNode:
            reg1 = sortRegs(regs1)[0]
            reg2 = reg1
         else:
            if len(regs2) == 1:
               reg2 = sortRegs(regs2)[0]
               otherRegs = filter(lambda x: (x in GPRRegs and regTo64(x)!=regTo64(reg2)) or (x not in GPRRegs and x[1:]!=reg2[1:]), regs1)
               if otherRegs:
                  reg1 = sortRegs(otherRegs)[0]
               else:
                  reg1 = sortRegs(regs1)[0]
            else:
               reg1 = sortRegs(regs1)[0]
               if not useDistinctRegs and reg1 in regs2:
                  reg2 = reg1
               else:
                  otherRegs = filter(lambda x: (x in GPRRegs and regTo64(x)!=regTo64(reg1)) or (x not in GPRRegs and x[1:]!=reg1[1:]), regs2)
                  if otherRegs:
                     reg2 = sortRegs(otherRegs)[0]
                  else:
                     reg2 = sortRegs(regs2)[0]

         instrI = getInstrInstanceFromNode(instrNode, useDistinctRegs=useDistinctRegs, opRegDict={startNodeIdx:reg1, targetNodeIdx:reg2})

         if reg1 == reg2:
            configList.append(LatConfig(instrI))

         reg1Prefix = re.sub('\d', '', reg1)
         reg2Prefix = re.sub('\d', '', reg2)

         if reg1 in GPRRegs and reg2 in GPRRegs:
            # MOVSX avoids partial reg stalls and cannot be eliminated by "move elimination"
            chainInstrs = 'MOVSX {}, {};'.format(regTo64(reg1), regToSize(reg2, min(32, getRegSize(reg2))))
            chainInstrs += 'MOVSX {}, {};'.format(regTo64(reg1), regTo32(reg1)) * cRep
            configList.append(LatConfig(instrI, chainInstrs=chainInstrs, chainLatency=basicLatency['MOVSX']*(cRep+1)))
         elif reg1Prefix == 'K' and reg2Prefix == 'K':
            chainInstr = 'KMOVQ {}, {};'.format(reg1, reg2)
            chainInstr += 'KMOVQ {0}, {0};'.format(reg1) * cRep
            configList.append(LatConfig(instrI, chainInstrs=chainInstr, chainLatency=basicLatency['KMOVQ']*(cRep+1)))
         elif reg1Prefix == 'K' and reg2Prefix[1:] == 'MM':
            # we test with both VPMOVQ2M and VPTESTNMQ (as, e.g., VPMAXUB ZMM has a higher latency with the former for some unknown reason)
            chainInstr1 = 'VPMOVQ2M ' + reg1 + ', ' + reg2 + ';'
            configList.append(LatConfig(instrI, chainInstrs=chainInstr1, chainLatency=basicLatency['VPMOVQ2M_'+reg2Prefix]))
            chainInstr2 = 'VPTESTNMQ ' + reg1 + ' {' + reg1 + '}, ' + reg2 + ', ' + reg2 + ';'
            configList.append(LatConfig(instrI, chainInstrs=chainInstr2, chainLatency=basicLatency['VPTESTNMQ_'+reg2Prefix]))
         elif reg1Prefix[1:] == 'MM' and reg2Prefix == 'K':
            chainInstr = 'VMOVUPS ' + reg1 + ' {' + reg2 + '}, ' + reg1Prefix + '14;'
            configList.append(LatConfig(instrI, chainInstrs=chainInstr, chainLatency=basicLatency['VMOVUPS_'+reg1Prefix+'_K']))
         elif reg1Prefix[1:] == reg2Prefix[1:]:
            # if the registers have different widths, bring the smaller to the width of the larger
            reg1 = reg1.replace(reg1Prefix, min(reg1Prefix, reg2Prefix))
            reg2 = reg2.replace(reg2Prefix, min(reg1Prefix, reg2Prefix))

            if reg1Prefix =='MM':
               chainInstr = 'MOVQ {}, {};'.format(reg1, reg2)
               chainInstr += 'MOVQ {0}, {0};'.format(reg1) * cRep
               configList.append(LatConfig(instrI, chainInstrs=chainInstr, chainLatency=basicLatency['MOVQ']*(cRep+1)))
            elif reg1Prefix in ['XMM', 'YMM', 'ZMM']:
               chainInstrFP, chainLatencyFP = getChainInstrForVectorRegs(instrNode, reg2, reg1, cRep, 'FP')
               configList.append(LatConfig(instrI, chainInstrs=chainInstrFP, chainLatency=chainLatencyFP))

               if not (reg1Prefix == 'YMM' and instrNode.attrib['extension'] == 'AVX'): # integers in YMM registers are only supported by AVX>=2
                  chainInstrInt, chainLatencyInt = getChainInstrForVectorRegs(instrNode, reg2, reg1, cRep, 'Int')
                  configList.append(LatConfig(instrI, chainInstrs=chainInstrInt, chainLatency=chainLatencyInt))
            else:
               print 'invalid reg prefix: ' + reg1Prefix
               return None
         else:
            configList.isUpperBound = True
            # find all other instrs from reg2 to reg1
            for chainInstrI in getAllChainInstrsFromRegToReg(instrNode, reg2, reg1):
               configList.append(LatConfig(instrI, chainInstrs=chainInstrI.asm, chainLatency=1))
      elif targetNode.attrib['type'] == 'flags':
         #################
         # reg -> flags
         #################

         reg = sortRegs(regs1)[0]

         for flag in STATUSFLAGS_noAF:
            if not ('flag_'+flag) in targetNode.attrib: continue
            if not 'w' in targetNode.attrib[('flag_'+flag)]: continue

            if reg in GPRRegs:
               regSize = getRegSize(reg)
               if regSize == 8:
                  chainInstr = 'SET{} {};'.format(flag[0], reg)
                  chainLatency = basicLatency['SET' + flag[0]]
               else:
                  chainInstr = 'CMOV{} {}, {};'.format(flag[0], reg, regToSize('R15', regSize))
                  chainLatency = basicLatency['CMOV' + flag[0]]
               instrI = getInstrInstanceFromNode(instrNode, ['R15'], ['R15'], useDistinctRegs, {startNodeIdx:reg})

               movsxInstr = 'MOVSX {}, {};'.format(regTo64(reg), regToSize(reg, min(32, regSize)))
               chainInstrs = chainInstr + movsxInstr * cRep
               chainLatency = chainLatency + basicLatency['MOVSX'] * cRep

               configList.append(LatConfig(instrI, chainInstrs=chainInstrs, chainLatency=chainLatency))
            elif 'MM' in reg:
               instrI = getInstrInstanceFromNode(instrNode, ['R13', 'R15'], ['R13', 'R15'], True, {startNodeIdx:reg})
               configList.isUpperBound = True
               for chainInstrI in getAllChainInstrsFromRegToReg(instrNode, 'R13', reg):
                  chainInstrs = 'CMOV' + flag[0] + ' R13, R15; ' + chainInstrI.asm
                  chainLatency = basicLatency['CMOV' + flag[0]] + 1
                  configList.append(LatConfig(instrI, chainInstrs=chainInstrs, chainLatency=chainLatency))
      elif targetNode.attrib['type'] == 'mem':
         #################
         # reg -> mem
         #################

         reg = sortRegs(regs1)[0]
         addrReg = getAddrReg(instrNode, targetNode)

         if reg in GPRRegs:
            instrI = getInstrInstanceFromNode(instrNode, useDistinctRegs=useDistinctRegs, opRegDict={startNodeIdx:reg})

            configList.isUpperBound = True
            chainInstrs = 'MOV {}, [{}];'.format(reg, addrReg)
            chainInstrs += 'MOVSX {}, {};'.format(regTo64(reg), regToSize(reg, min(32, getRegSize(reg)))) * cRep
            chainLatency = int(basicLatency['MOV_10MOVSX_MOV_'+str(getRegSize(reg))] >= 12) # 0 if CPU supports zero-latency store forwarding
            chainLatency += basicLatency['MOVSX'] * cRep

            if re.search('BT.*MEMv_GPRv', instrNode.attrib['iform']):
               chainInstrs += 'AND ' + reg + ', 0;'
               chainLatency += basicLatency['AND']

            configList.append(LatConfig(instrI, chainInstrs=chainInstrs, chainLatency=chainLatency))
         elif 'MM' in reg:
            if suppressedTarget:
               # ToDo: only happens in the case of maskmovdqu
               pass
            else:
               instrI = getInstrInstanceFromNode(instrNode, useDistinctRegs=True, opRegDict={startNodeIdx:reg})
               configList.isUpperBound = True
               configList.extend(getLatConfigsFromMemToReg(instrNode, instrI, targetNode, reg, addrReg, cRep))
         else:
            # ToDo
            print 'unsupported reg to mem'
            return None
   elif startNode.attrib['type'] == 'flags':
      #################
      # flags -> ...
      #################
      if targetNode.attrib['type'] == 'reg':
         #################
         # flags -> reg
         #################
         regs = set(targetNode.text.split(','))-globalDoNotWriteRegs-specialRegs
         if not regs: return None

         reg = sortRegs(regs)[0]

         if reg in GPRRegs:
            instrI = getInstrInstanceFromNode(instrNode, useDistinctRegs=useDistinctRegs, opRegDict={targetNodeIdx:reg})
            chainInstrs = 'TEST {0}, {0};'.format(reg)
            chainLatency = basicLatency['TEST']
            configList.append(LatConfig(instrI, chainInstrs=chainInstrs, chainLatency=chainLatency))

            chainInstrs = 'MOVSX {}, {};'.format(regTo64(reg), regToSize(reg, min(32, getRegSize(reg)))) * cRep + chainInstrs
            chainLatency += basicLatency['MOVSX'] * cRep
            configList.append(LatConfig(instrI,  chainInstrs=chainInstrs, chainLatency=chainLatency))
         else:
            # ToDo: there is no instruction from flag to vector reg; the only non-GPR that is possible are ST(0) and X87STATUS
            return None
      elif targetNode.attrib['type'] == 'flags':
         #################
         # flags -> flag
         #################

         instrI = getInstrInstanceFromNode(instrNode, useDistinctRegs=useDistinctRegs)
         configList.append(LatConfig(instrI))

         cfModifiers = startNode.attrib.get('flag_CF', '')
         if 'r' in cfModifiers and 'w' in cfModifiers:
            chainInstrs = 'CMC;'*cRep
            configList.append(LatConfig(instrI, chainInstrs=chainInstrs, chainLatency=basicLatency['CMC']*cRep))
      elif targetNode.attrib['type'] == 'mem':
         #################
         # flags -> mem
         #################
         instrI = getInstrInstanceFromNode(instrNode, useDistinctRegs=useDistinctRegs)
         chainInstr = 'TEST ' + targetNode.attrib['memory-prefix'] + ' [' + getAddrReg(instrNode, targetNode) + '], 1'
         configList.isUpperBound = True
         configList.append(LatConfig(instrI, chainInstrs=chainInstr, chainLatency=1))
   elif startNode.attrib['type'] == 'mem':
      #################
      # mem -> ...
      #################
      if startNode.attrib.get('r', '0') == '0' and targetNode != startNode:
         # for memory writes, only the dependency address -> memory is interesting
         return None

      addrReg = getAddrReg(instrNode, startNode)
      memWidth = int(startNode.attrib['width'])

      if targetNode.attrib['type'] == 'reg':
         #################
         # mem -> reg
         #################
         regs = set(targetNode.text.split(","))
         if not suppressedTarget: regs -= globalDoNotWriteRegs | specialRegs
         if not regs: return None
         reg = sortRegs(regs)[0]
         regSize = getRegSize(reg)

         if suppressedStart:
            if not regs.issubset(GPRRegs):
               print 'read from suppressed mem to non-GPR reg not yet supported'
               return None

         if reg in GPRRegs:
            instrI = getInstrInstanceFromNode(instrNode, [addrReg, 'R12'], [addrReg, 'R12'], useDistinctRegs, {targetNodeIdx:reg})

            if addr_mem == 'addr':
               # addr -> reg
               chainInstrs = 'MOVSX ' + regTo64(reg) + ', ' + regToSize(reg, min(32, regSize)) + ';'
               chainInstrs += 'XOR {}, {};'.format(addrReg, regTo64(reg)) * cRep + ('TEST R13, R13;' if instrReadsFlags else '') # cRep is a multiple of 2
               chainLatency = basicLatency['MOVSX'] + basicLatency['XOR'] * cRep
               configList.append(LatConfig(instrI, chainInstrs=chainInstrs, chainLatency=chainLatency))
            else:
               # mem -> reg
               configList = LatConfigList()
               configList.isUpperBound = True
               chainInstrs = 'MOVSX R12, {};'.format(regToSize(reg, min(32, regSize)))
               chainInstrs += 'MOVSX R12, R12d;' * (cRep-1)
               chainInstrs += 'mov [{}], {};'.format(addrReg, regToSize('R12', regSize))
               chainLatency = basicLatency['MOVSX'] * cRep
               chainLatency += int(basicLatency['MOV_10MOVSX_MOV_'+str(regSize)] >= 12) # 0 if CPU supports zero-latency store forwarding
               configList.append(LatConfig(instrI, chainInstrs=chainInstrs, chainLatency=chainLatency))
         elif 'MM' in reg:
            instrI = getInstrInstanceFromNode(instrNode, ['R12'], ['R12'], useDistinctRegs, {targetNodeIdx:reg})

            if addr_mem == 'addr':
               # addr -> reg
               configList.isUpperBound = True
               chainInstrs = 'MOVQ R12, {};'.format(getCanonicalReg(reg))
               if isAVXInstr(instrNode):
                  chainInstrs = 'V' + chainInstrs
               chainInstrs += 'XOR {}, {};'.format(addrReg, 'R12') * cRep + ('TEST R13, R13;' if instrReadsFlags else '') # cRep is a multiple of 2
               chainLatency = 1 + basicLatency['XOR'] * cRep
               configList.append(LatConfig(instrI, chainInstrs=chainInstrs, chainLatency=chainLatency))
            elif addr_mem == 'addr_VSIB':
               # addr_VSIB -> reg
               configList.isUpperBound = True
               chainInstrs = 'VANDPD {0}14, {0}14, {0}{1};'.format(startNode.attrib['VSIB'], reg[3:]) * cRep
               chainLatency = basicLatency['VANDPD'] * cRep
               configList.append(LatConfig(instrI, chainInstrs=chainInstrs, chainLatency=chainLatency))
            else:
               # mem -> reg
               configList.isUpperBound = True
               configList.extend(getLatConfigsFromRegToMem(instrNode, instrI, reg, addrReg, memWidth, cRep))
      elif targetNode.attrib['type'] == 'flags':
         #################
         # mem -> flags
         #################
         for flag in STATUSFLAGS_noAF:
            if not ('flag_'+flag) in targetNode.attrib: continue
            if not 'w' in targetNode.attrib[('flag_'+flag)]: continue

            instrI = getInstrInstanceFromNode(instrNode, [addrReg, 'R12'], [addrReg, 'R12'], useDistinctRegs)

            if addr_mem == 'addr':
               # addr -> flag
               chainInstr = 'CMOV' + flag[0] + ' ' + addrReg + ', ' + addrReg
               chainLatency = basicLatency['CMOV' + flag[0]]
               configList.append(LatConfig(instrI, chainInstrs=chainInstr, chainLatency=chainLatency))
            else:
               # mem -> flag
               if memWidth <= 64:
                  configList.isUpperBound = True
                  chainInstrs = 'CMOV' + flag[0] + ' R12, R12;'
                  chainInstrs += 'MOVSX R12, R12d;' * cRep
                  chainInstrs += 'mov [' + addrReg + '], ' + regToSize('R12', memWidth)
                  chainLatency = basicLatency['CMOV' + flag[0]] + basicLatency['MOVSX'] * cRep
                  chainLatency += int(basicLatency['MOV_10MOVSX_MOV_'+str(memWidth)] >= 12) # 0 if CPU supports zero-latency store forwarding
                  configList.append(LatConfig(instrI, chainInstrs=chainInstrs, chainLatency=chainLatency))
               else:
                  # ToDo
                  pass
      elif targetNode.attrib['type'] == 'mem':
         #################
         # mem -> mem
         #################
         if startNode == targetNode:
            instrI = getInstrInstanceFromNode(instrNode, [addrReg, 'R12'], [addrReg, 'R12'], useDistinctRegs=useDistinctRegs)

            if addr_mem == 'addr':
               # addr -> mem
               configList.isUpperBound = True
               chainInstrs = 'MOV ' + regToSize('R12', min(64, memWidth)) + ', [' + addrReg + '];'
               chainInstrs += ('MOVSX R12, ' + regToSize('R12', min(32, memWidth)) + ';') * cRep
               chainInstrs += 'XOR ' + addrReg + ', R12; XOR ' + addrReg + ', R12;' + ('TEST R13, R13;' if instrReadsFlags else '')
               chainLatency = basicLatency['MOVSX'] * cRep + 2*basicLatency['XOR']
               chainLatency += int(basicLatency['MOV_10MOVSX_MOV_'+str(min(64, memWidth))] >= 12) # 0 if CPU supports zero-latency store forwarding
               configList.append(LatConfig(instrI, chainInstrs=chainInstrs, chainLatency=chainLatency))
            else:
               # mem -> mem
               if startNode.attrib.get('r','0')=='1':
                  configList = LatConfigList()
                  configList.append(LatConfig(instrI))

                  if memWidth <= 64:
                     chainInstrs = 'MOV ' + regToSize('R12', min(64, memWidth)) + ', [' + addrReg + '];'
                     chainInstrs += ('MOVSX R12, ' + regToSize('R12', min(32, memWidth)) + ';')*10
                     chainInstrs += ('MOV [' + addrReg + '], ' + regToSize('R12', min(64, memWidth)))
                     chainLatency = basicLatency['MOV_10MOVSX_MOV_'+str(min(64, memWidth))]
                     configList.append(LatConfig(instrI, chainInstrs=chainInstrs, chainLatency=chainLatency))
                  else:
                     # ToDo
                     pass
         else:
            # ToDo
            return None

   if not configList.latConfigs: return None
   return [configList]


def getLatencies(instrNode, instrNodeList, tpDict, htmlReports):
   if useIACA:
      createIacaAsmFile("/tmp/ramdisk/asm.s", "", 0, getInstrInstanceFromNode(instrNode).asm)

      if iacaVersion == '2.1':
         try:
            subprocess.check_output(['as', '/tmp/ramdisk/asm.s', '-o', '/tmp/ramdisk/asm.o'])
            iaca_lat = subprocess.check_output(iacaCMDLine + ['-analysis', 'LATENCY', '/tmp/ramdisk/asm.o'], stderr=subprocess.STDOUT)
         except subprocess.CalledProcessError as e:
            print "Error: " + e.output
            return None

         if '!' in iaca_lat or not 'Latency' in iaca_lat:
            print "IACA error"
            return None

         latency = iaca_lat.split('\n')[3].split()[1]

         htmlReports.append('<pre>' + iaca_lat + '</pre>\n')

         return latency
   else:
      if instrNode.attrib['iclass'] in ['CALL_NEAR', 'CALL_NEAR_MEMv', 'CLZERO', 'JMP', 'JMP_MEMv', 'RET_NEAR', 'RET_NEAR_IMMw', 'RDMSR', 'WRMSR', 'RDPMC', 'LEAVE', 'CPUID', 'POPF', 'POPFQ']:
         return None;
      if 'REP' in instrNode.attrib['iclass']:
         return None;
      if instrNode.attrib['category'] in ['IO', 'IOSTRINGOP', 'PKU']:
         return None

      inputOpnds = []
      outputOpnds = []

      for opNode in instrNode.iter('operand'):
         if opNode.attrib['type'] == 'flags' and not any(('flag_'+f in opNode.attrib) for f in STATUSFLAGS_noAF):
            continue

         if opNode.attrib.get('r', '0') == '1':
            inputOpnds.append(opNode)
         if opNode.attrib.get('w', '0') == '1':
            outputOpnds.append(opNode)
            if opNode.attrib['type'] == 'mem' and opNode.attrib.get('r', '0') == '0':
               inputOpnds.append(opNode) # address of memory write
            if opNode.attrib['type'] == 'reg' and opNode.attrib.get('conditionalWrite', '0') == '1':
               inputOpnds.append(opNode)

      archNode = instrNode.find('./architecture[@name="' + arch + '"]')
      measurementNode = archNode.find('./measurement')

      canUseSameRegForDifferentOpnds = False
      if not 'GATHER' in instrNode.attrib['category'] and not 'SCATTER' in instrNode.attrib['category']:
         for opNode1 in instrNode.findall('./operand[@type="reg"]'):
            for opNode2 in instrNode.findall('./operand[@type="reg"]'):
               if opNode2 == opNode1: continue
               if opNode1.attrib.get('suppressed', '') == '1' or opNode2.attrib.get('suppressed', '') == '1': continue
               if opNode1.text == opNode2.text:
                  canUseSameRegForDifferentOpnds = True
                  break

      overallMaxLat = 0

      htmlHead = []
      htmlBottom = []

      for opNode1 in inputOpnds:
         opNode1Idx = int(opNode1.attrib['idx'])

         for opNode2 in outputOpnds:
            opNode2Idx = int(opNode2.attrib['idx'])
            latencyNode = None

            for addr_mem in (['addr', 'mem']+(['addr_VSIB'] if 'VSIB' in opNode1.attrib else []) if opNode1.attrib['type']=='mem' else ['']):
               minLatDistinctRegs = 0
               maxLatDistinctRegs = 0

               configI = 0
               for useDistinctRegs in ([True, False] if canUseSameRegForDifferentOpnds else [True]):
                  latConfigLists = getLatConfigLists(instrNode, opNode1, opNode2, useDistinctRegs, addr_mem, tpDict)
                  if latConfigLists is None: continue

                  minLat = sys.maxint
                  maxLat = 0

                  minLatIsUpperBound = False
                  maxLatIsUpperBound = False

                  configHtmlReports = []

                  for latConfigList in latConfigLists:
                     minLatForCurList = sys.maxint

                     if not any((latConfig.init or latConfig.instrI.regMemInit) for latConfig in latConfigList.latConfigs):
                        # Test different register values for read-only registers
                        for readOnlyRegOpNode in instrNode.findall('./operand[@type="reg"][@r="1"]'):
                           if readOnlyRegOpNode == opNode1: continue
                           if readOnlyRegOpNode.attrib.get('w', '') == '1': continue
                           readOnlyRegOpNodeIdx = int(readOnlyRegOpNode.attrib['idx'])
                           for latConfig in list(latConfigList.latConfigs):
                              if not readOnlyRegOpNodeIdx in latConfig.instrI.opRegDict:
                                 print 'readOnlyRegOpNodeIdx not found in opRegDict'
                                 continue
                              reg = latConfig.instrI.opRegDict[readOnlyRegOpNodeIdx]
                              if not reg in GPRRegs or reg in globalDoNotWriteRegs or reg in specialRegs: continue
                              if any((opNode is not None) for opNode in instrNode.findall('./operand[@type="reg"][@w="1"]')
                                     if regTo64(latConfig.instrI.opRegDict[int(opNode.attrib['idx'])]) == regTo64(reg)): continue

                              latConfigList.latConfigs.remove(latConfig)
                              for regVal in ['0', '1', '2']:
                                 newlatConfig = copy.deepcopy(latConfig)
                                 newlatConfig.init += ['MOV ' + reg + ', ' + regVal]
                                 newlatConfig.notes.append('with ' + reg + '='  + regVal)
                                 latConfigList.latConfigs.append(newlatConfig)

                     # Create a copy of each experiment with dependency-breaking instructions for all dependencies other than the dependency from opNode2 to
                     # opNode1 if there aren't sufficiently many fill instructions in the chain
                     if (not 'DIV' in instrNode.attrib['iclass'] and not 'SQRT' in instrNode.attrib['iclass'] and
                           not 'GATHER' in instrNode.attrib['category'] and not 'SCATTER' in instrNode.attrib['category']):
                        for latConfig in list(latConfigList.latConfigs):
                           if latConfig.chainLatency > tpDict[instrNode].TP_single:
                              continue

                           depBreakingInstrs = getDependencyBreakingInstrs(instrNode, latConfig.instrI.opRegDict)
                           if not depBreakingInstrs: continue

                           newlatConfig = copy.deepcopy(latConfig)
                           depBreakingAdded = False
                           for depOpNode in depBreakingInstrs:
                              depOpNodeIdx = int(depOpNode.attrib['idx'])
                              if (depOpNodeIdx in latConfig.instrI.opRegDict and opNode1Idx in latConfig.instrI.opRegDict
                                   and latConfig.instrI.opRegDict[depOpNodeIdx] == latConfig.instrI.opRegDict[opNode1Idx]):
                                 continue
                              elif depOpNode == opNode1 and opNode1 == opNode2:
                                 continue
                              elif opNode1.attrib['type'] == 'flags' and depOpNode.attrib['type'] == 'flags':
                                 continue
                              elif depOpNode == opNode1:
                                 # ToDo: this might be unnecessary, if CMOVcc wouldn't use the same regs
                                 newlatConfig.chainInstrs = depBreakingInstrs[depOpNode] + '; ' + latConfig.chainInstrs
                                 depBreakingAdded = True
                              else:
                                 if not latConfig.chainInstrs.endswith(depBreakingInstrs[depOpNode]):
                                    newlatConfig.chainInstrs = latConfig.chainInstrs + ';' + depBreakingInstrs[depOpNode]
                                    depBreakingAdded = True
                           if depBreakingAdded:
                              latConfigList.latConfigs.remove(latConfig)
                              latConfigList.latConfigs.append(latConfig) # order ...
                              newlatConfig.notes.append('with dependency-breaking instructions')
                              latConfigList.latConfigs.append(newlatConfig)

                     # make sure that the mask for gather/scatter instruction is never empty
                     if instrNode.attrib['extension'] == 'AVX2GATHER':
                        for latConfig in latConfigList.latConfigs:
                           maskReg = latConfig.instrI.opRegDict[3]
                           if opNode1Idx == 3:
                              latConfig.chainInstrs += 'VPCMPEQD {0}, {0}, {0};'.format(maskReg[0:3] + '13')
                              if 'VSHUFPD' in latConfig.chainInstrs:
                                 orInstr = 'VORPD'
                              else:
                                 orInstr = 'VPOR'
                              latConfig.chainInstrs += '{0} {1}, {1}, {2};'.format(orInstr, maskReg, maskReg[0:3] + '13')
                              latConfig.chainLatency += basicLatency[orInstr]
                           else:
                              latConfig.chainInstrs += 'VPCMPEQD {0}, {0}, {0};'.format(maskReg)
                     elif instrNode.attrib['extension'] == 'AVX512EVEX' and ('GATHER' in instrNode.attrib['category'] or 'SCATTER' in instrNode.attrib['category']):
                        for latConfig in latConfigList.latConfigs:
                           maskReg = latConfig.instrI.opRegDict[2]
                           if opNode1Idx == 2:
                              # ToDo
                              pass
                           else:
                              latConfig.chainInstrs += 'VPCMPD {0}, {1}, {1}, 7;'.format(maskReg, 'XMM15')

                     mlDP = sys.maxint
                     mlnoDP = sys.maxint

                     for latConfig in latConfigList.latConfigs:
                        configI += 1
                        configHtmlReports.append('<h3>Experiment ' + str(configI))
                        if latConfig.notes or not useDistinctRegs:
                           configHtmlReports.append(' (' + ', '.join(latConfig.notes +
                                                    (['with the same register for different operands'] if not useDistinctRegs else [])) + ')')
                        configHtmlReports.append('</h3>\n')

                        configHtmlReports.append('<ul>\n')
                        configHtmlReports.append('<li>Instruction: <code>' + latConfig.instrI.asm + '</code></li>\n')
                        if latConfig.chainInstrs:
                           chainIStr = latConfig.chainInstrs.strip(';')
                           configHtmlReports.append('<li>Chain instruction' + ('s' if ';' in chainIStr else '') + ': <code>' + chainIStr + '</code></li>\n')
                        if latConfig.chainLatency:
                           configHtmlReports.append('<li>Chain latency: ' + ('&ge;' if latConfigList.isUpperBound else '') + str(latConfig.chainLatency) + '</li>\n')

                        init = latConfig.instrI.regMemInit + latConfig.init
                        measurementResult = runExperiment(instrNode, latConfig.instrI.asm + ';' + latConfig.chainInstrs, init=init, htmlReports=configHtmlReports, unrollCount=100)
                        configHtmlReports.append('</ul>\n')

                        if not measurementResult:
                           print 'no result found'
                           continue

                        cycles = measurementResult['Core cycles']

                        cycles = int(cycles+.2)

                        if latConfig.chainLatency:
                           cycles -= latConfig.chainLatency

                        cycles = max(0, cycles) # for dep. breaking instructions (like XOR), cycles might be negative after subtracting chainLatency

                        minLatForCurList = min(minLatForCurList, cycles)

                     if minLatForCurList < minLat:
                        minLat = minLatForCurList
                        minLatIsUpperBound = latConfigList.isUpperBound

                     if minLatForCurList > maxLat:
                        maxLat = minLatForCurList
                        maxLatIsUpperBound = latConfigList.isUpperBound

                  if minLat > maxLat: continue

                  if useDistinctRegs:
                     minLatDistinctRegs = minLat
                     maxLatDistinctRegs = maxLat
                  else:
                     if minLatDistinctRegs == minLat and maxLatDistinctRegs == maxLat:
                        htmlBottom.append('<div style="margin-left: 50px">')
                        htmlBottom += configHtmlReports
                        htmlBottom.append('</div>')
                        continue

                  overallMaxLat = max(overallMaxLat, maxLat)

                  if latencyNode is None:
                     latencyNode = SubElement(measurementNode, 'latency')
                     latencyNode.attrib['start_op'] = str(opNode1.attrib['idx'])
                     latencyNode.attrib['target_op'] = str(opNode2.attrib['idx'])

                  suffix = ('_'+addr_mem if addr_mem else '') + ('_same_reg' if not useDistinctRegs else '')
                  if minLat == maxLat:
                     latencyNode.attrib['cycles'+suffix] = str(minLat)
                     if minLatIsUpperBound:
                        latencyNode.attrib['cycles'+suffix+'_is_upper_bound'] = '1'
                  else:
                     latencyNode.attrib['min_cycles'+suffix] = str(minLat)
                     if minLatIsUpperBound:
                        latencyNode.attrib['min_cycles'+suffix+'_is_upper_bound'] = '1'
                     latencyNode.attrib['max_cycles'+suffix] = str(maxLat)
                     if maxLatIsUpperBound:
                        latencyNode.attrib['max_cycles'+suffix+'_is_upper_bound'] = '1'

                  summaryLine = latencyNodeToStr(latencyNode, not useDistinctRegs, addr_mem)

                  h2ID = 'lat' + str(opNode1Idx) + '->' + str(opNode2Idx) + suffix
                  htmlHead.append('<a href="#' + h2ID + '"><h3>' + summaryLine + '</h3></a>')
                  if useDistinctRegs: htmlBottom.append('<hr>')
                  htmlBottom.append('<h2 id="' + h2ID + '">' + summaryLine + '</h2>')
                  htmlBottom.append('<div style="margin-left: 50px">')
                  htmlBottom += configHtmlReports
                  htmlBottom.append('</div>')

      addHTMLCodeForOperands(instrNode, htmlReports)
      htmlReports.append('<hr>')
      htmlReports += htmlHead
      htmlReports += htmlBottom

      return overallMaxLat


def isSSEInstr(instrNode):
   extension = instrNode.attrib['extension']
   return 'SSE' in extension or extension in ['AES']


def isAVXInstr(instrNode):
   return ('vex' in instrNode.attrib or 'evex' in instrNode.attrib)


def writeHtmlFile(folder, instrNode, title, body):
   filename = canonicalizeInstrString(instrNode.attrib['string'])
   if useIACA:
      filename += '-IACA' + iacaVersion
   else:
      filename += '-Measurements'
   filename += '.html'

   folder = '/tmp/cpu-html/' + folder
   if not os.path.exists(folder):
      os.makedirs(folder)
   with open(os.path.join(folder, filename), "w") as f:
      f.write('<html>\n'
              '<head>\n'
              '<title>' + title + '</title>\n'
              '</head>\n'
              '<body>\n'
              + body +
              '</body>\n'
              '</html>\n')


# returns list of xml instruction nodes
def filterInstructions(XMLRoot):
   allInstrs = list(XMLRoot.iter('instruction'))

   instrSet = set(allInstrs)
   for XMLInstr in allInstrs:
      extension = XMLInstr.attrib['extension']
      isaSet = XMLInstr.attrib['isa-set']

      # Future instruction set extensions
      if extension in ['CET', 'RDPRU']: instrSet.discard(XMLInstr)

      # Not supported by assembler
      if XMLInstr.attrib['iclass'] == 'NOP' and len(XMLInstr.findall('operand')) > 1:
         instrSet.discard(XMLInstr)
      if extension in ['WBNOINVD']: instrSet.discard(XMLInstr)

      # Only supported by VIA
      if 'VIA_' in extension:
         instrSet.discard(XMLInstr)

      # "no CPU available today has PTWRITE support" (https://software.intel.com/en-us/forums/intel-isa-extensions/topic/704356)
      if extension in ['PT']:
         instrSet.discard(XMLInstr)

      if useIACA:
         if extension in ['AVX512VEX', 'AVX512EVEX'] and arch != 'SKX': instrSet.discard(XMLInstr)
         # AMD
         if extension in ['3DNOW', 'CLZERO', 'FMA4', 'MONITORX', 'SSE4a', 'SVM', 'TBM', 'XOP']: instrSet.discard(XMLInstr)
         # Future instruction set extensions
         if extension in ['CLDEMOTE', 'ENQCMD', 'MOVDIR', 'PCONFIG', 'WAITPKG']: instrSet.discard(XMLInstr)
         if extension in ['AVX512EVEX'] and any(x in isaSet for x in ['4FMAPS', '4VNNIW', 'ER', 'PF']): instrSet.discard(XMLInstr)
         if any(x in isaSet for x in ['BF16', 'BITALG', 'GFNI', 'VAES', 'VBMI2', 'VNNI', 'VP2INTERSECT', 'VPCLMULQDQ', 'VPOPCNTDQ']): instrSet.discard(XMLInstr)

   if useIACA: return list(instrSet)

   cpu = cpuid.CPUID()

   _, _, ecx1, edx1 = cpu(0x01)
   _, ebx7, ecx7, edx7 = cpu(0x07)
   eax7_1, _, _, _ = cpu(0x07, 0x01)
   _, _, ecx8_1, edx8_1 = cpu(0x80000001)
   _, ebx8_8, _, _ = cpu(0x80000008)

   for XMLInstr in allInstrs:
      iclass = XMLInstr.attrib['iclass']
      extension = XMLInstr.attrib['extension']
      isaSet = XMLInstr.attrib['isa-set']
      category = XMLInstr.attrib['category']

      if extension == 'SSE3' and not cpuid.get_bit(ecx1, 0): instrSet.discard(XMLInstr)
      if extension == 'PCLMULQDQ' and not cpuid.get_bit(ecx1, 1): instrSet.discard(XMLInstr)
      if extension == 'SSSE3' and not cpuid.get_bit(ecx1, 9): instrSet.discard(XMLInstr)
      if extension == 'FMA' and not cpuid.get_bit(ecx1, 12): instrSet.discard(XMLInstr)
      if extension == 'SSE4' and not cpuid.get_bit(ecx1, 19): instrSet.discard(XMLInstr)
      if isaSet == 'SSE42' and not cpuid.get_bit(ecx1, 20): instrSet.discard(XMLInstr)
      if extension == 'MOVBE' and not cpuid.get_bit(ecx1, 22): instrSet.discard(XMLInstr)
      if isaSet == 'POPCNT' and not cpuid.get_bit(ecx1, 23): instrSet.discard(XMLInstr)
      if extension == 'AES' and not cpuid.get_bit(ecx1, 25): instrSet.discard(XMLInstr)
      if extension == 'AVX':
         if not cpuid.get_bit(ecx1, 28):
            instrSet.discard(XMLInstr)
         else:
            global supportsAVX
            supportsAVX = True
      if extension == 'AVXAES' and not (cpuid.get_bit(ecx1, 25) and cpuid.get_bit(ecx1, 28)): instrSet.discard(XMLInstr)
      if extension == 'F16C' and not cpuid.get_bit(ecx1, 29): instrSet.discard(XMLInstr)
      if extension == 'RDRAND' and not cpuid.get_bit(ecx1, 30): instrSet.discard(XMLInstr)
      if extension == 'MMX' and not cpuid.get_bit(edx1, 23): instrSet.discard(XMLInstr)
      if extension == 'SSE' and not cpuid.get_bit(edx1, 25): instrSet.discard(XMLInstr)
      if extension == 'SSE2' and not cpuid.get_bit(edx1, 26): instrSet.discard(XMLInstr)
      if extension == 'BMI1' and not cpuid.get_bit(ebx7, 3): instrSet.discard(XMLInstr)
      if extension in ['AVX2', 'AVX2GATHER'] and not cpuid.get_bit(ebx7, 5): instrSet.discard(XMLInstr)
      if extension == 'BMI2' and not cpuid.get_bit(ebx7, 8): instrSet.discard(XMLInstr)
      if extension == 'WBNOINVD' and not cpuid.get_bit(ebx7, 9): instrSet.discard(XMLInstr)
      if extension == 'MPX' and not cpuid.get_bit(ebx7, 14): instrSet.discard(XMLInstr)
      if isaSet.startswith('AVX512F') and not cpuid.get_bit(ebx7, 16): instrSet.discard(XMLInstr)
      if isaSet.startswith('AVX512DQ') and not cpuid.get_bit(ebx7, 16): instrSet.discard(XMLInstr)
      if extension == 'RDSEED' and not cpuid.get_bit(ebx7, 18): instrSet.discard(XMLInstr)
      if extension == 'ADOX_ADCX' and not cpuid.get_bit(ebx7, 19): instrSet.discard(XMLInstr)
      if isaSet.startswith('AVX512_IFMA') and not cpuid.get_bit(ebx7, 21): instrSet.discard(XMLInstr)
      if extension == 'CLFLUSHOPT' and not cpuid.get_bit(ebx7, 23): instrSet.discard(XMLInstr)
      if extension == 'CLWB' and not cpuid.get_bit(ebx7, 24): instrSet.discard(XMLInstr)
      if isaSet.startswith('AVX512PF') and not cpuid.get_bit(ebx7, 26): instrSet.discard(XMLInstr)
      if isaSet.startswith('AVX512ER') and not cpuid.get_bit(ebx7, 27): instrSet.discard(XMLInstr)
      if isaSet.startswith('AVX512CD') and not cpuid.get_bit(ebx7, 28): instrSet.discard(XMLInstr)
      if extension == 'SHA' and not cpuid.get_bit(ebx7, 29): instrSet.discard(XMLInstr)
      if isaSet.startswith('AVX512BW') and not cpuid.get_bit(ebx7, 30): instrSet.discard(XMLInstr)
      if extension == 'PREFETCHWT1' and not cpuid.get_bit(ecx7, 0): instrSet.discard(XMLInstr)
      if category == 'AVX512_VBMI' and not cpuid.get_bit(ecx7, 1): instrSet.discard(XMLInstr)
      if extension == 'PKU' and not cpuid.get_bit(ecx7, 4): instrSet.discard(XMLInstr)
      if extension == 'WAITPKG' and not cpuid.get_bit(ecx7, 5): instrSet.discard(XMLInstr)
      if isaSet.startswith('AVX512_VBMI2') and not cpuid.get_bit(ecx7, 6): instrSet.discard(XMLInstr)
      if category == 'GFNI' and not cpuid.get_bit(ecx7, 8): instrSet.discard(XMLInstr)
      if 'VAES' in isaSet and not cpuid.get_bit(ecx7, 9): instrSet.discard(XMLInstr)
      if 'VPCLMULQDQ' in isaSet and not cpuid.get_bit(ecx7, 10): instrSet.discard(XMLInstr)
      if isaSet.startswith('AVX512_VNNI') and not cpuid.get_bit(ecx7, 11): instrSet.discard(XMLInstr)
      if isaSet.startswith('AVX512_BITALG') and not cpuid.get_bit(ecx7, 12): instrSet.discard(XMLInstr)
      if isaSet.startswith('AVX512_VPOPCNTDQ') and not cpuid.get_bit(ecx7, 14): instrSet.discard(XMLInstr)
      if extension == 'RDPID' and not cpuid.get_bit(ecx7, 22): instrSet.discard(XMLInstr)
      if extension == 'CLDEMOTE' and not cpuid.get_bit(ecx7, 25): instrSet.discard(XMLInstr)
      if iclass == 'MOVDIRI' and not cpuid.get_bit(ecx7, 27): instrSet.discard(XMLInstr)
      if iclass == 'MOVDIR64B' and not cpuid.get_bit(ecx7, 28): instrSet.discard(XMLInstr)
      if extension == 'ENQCMD' and not cpuid.get_bit(ecx7, 29): instrSet.discard(XMLInstr)
      if isaSet.startswith('AVX512_4VNNI') and not cpuid.get_bit(edx7, 2): instrSet.discard(XMLInstr)
      if isaSet.startswith('AVX512_4FMAPS') and not cpuid.get_bit(edx7, 3): instrSet.discard(XMLInstr)
      if isaSet.startswith('AVX512_VP2INTERSECT') and not cpuid.get_bit(edx7, 8): instrSet.discard(XMLInstr)
      if extension == 'PCONFIG' and not cpuid.get_bit(edx7, 18): instrSet.discard(XMLInstr)
      if isaSet.startswith('AVX512_BF16') and not cpuid.get_bit(eax7_1, 5): instrSet.discard(XMLInstr)
      if extension == 'SSE4a' and not cpuid.get_bit(ecx8_1, 6): instrSet.discard(XMLInstr)
      if extension == 'XOP' and not cpuid.get_bit(ecx8_1, 11): instrSet.discard(XMLInstr)
      if extension == 'FMA4' and not cpuid.get_bit(ecx8_1, 16): instrSet.discard(XMLInstr)
      if extension == 'TBM' and not cpuid.get_bit(ecx8_1, 21): instrSet.discard(XMLInstr)
      if extension == 'RDTSCP' and not cpuid.get_bit(edx8_1, 27): instrSet.discard(XMLInstr)
      if extension == '3DNOW' and not cpuid.get_bit(edx8_1, 31): instrSet.discard(XMLInstr)
      if extension in ['CLZERO']and not cpuid.get_bit(ebx8_8, 0): instrSet.discard(XMLInstr)

      # Virtualization instructions
      if extension in ['SVM', 'VMFUNC', 'VTX']: instrSet.discard(XMLInstr)

      # Safer Mode Extensions
      if extension in ['SMX']: instrSet.discard(XMLInstr)

      # Software Guard Extensions
      if extension in ['SGX', 'SGX_ENCLV']: instrSet.discard(XMLInstr)

      # Transactional Synchronization Extensions
      if extension in ['RTM']: instrSet.discard(XMLInstr)

      # X87 instructions:
      if extension in ['X87']: instrSet.discard(XMLInstr)
      if XMLInstr.attrib['category'] in ['X87_ALU']: instrSet.discard(XMLInstr)

      # System instructions
      if extension in ['INVPCID', 'MONITOR', 'MONITORX', 'RDWRFSGS', 'SMAP', 'XSAVE', 'XSAVEC', 'XSAVEOPT', 'XSAVES']: instrSet.discard(XMLInstr)
      if XMLInstr.attrib['category'] in ['INTERRUPT', 'SEGOP', 'SYSCALL', 'SYSRET']: instrSet.discard(XMLInstr)
      if XMLInstr.attrib['iclass'] in ['CALL_FAR', 'HLT', 'INVD', 'IRET', 'IRETD', 'IRETQ', 'JMP_FAR', 'LTR', 'RET_FAR', 'UD2']:
         instrSet.discard(XMLInstr)
      if XMLInstr.attrib['iform'] in ['POP_FS', 'POP_GS', 'MOV_CR_CR_GPR64', 'MOV_SEG_MEMw', 'MOV_SEG_GPR16', 'SWAPGS']: instrSet.discard(XMLInstr)

      # Undefined instructions
      if XMLInstr.attrib['iclass'].startswith('UD'): instrSet.discard(XMLInstr)

   return list(instrSet)


def main():
   parser = argparse.ArgumentParser(description='CPU Benchmarks')
   parser.add_argument("-iaca", help="IACA command line; if not specified, perf. ctrs. are used")
   parser.add_argument("-input", help="Instructions XML file", required=True)
   parser.add_argument("-output", help="Output XML file", default='result.xml')
   parser.add_argument("-arch", help="Architecture, Supported: [NHM, ...]")
   parser.add_argument("-noPretty", help="Disable pretty printing XML file", action='store_true')
   parser.add_argument("-noPorts", help="Don't measure port usage", action='store_true')
   parser.add_argument("-tpInput", help=".pickle file with TP data")
   parser.add_argument("-latInput", help=".pickle file with latency data")
   parser.add_argument("-debug", help="Debug output", action='store_true')

   args = parser.parse_args()

   global arch
   if args.arch is not None:
      arch = args.arch
   else:
      cpu = cpuid.CPUID()
      arch = cpuid.micro_arch(cpu)
      print cpuid.get_basic_info(cpu)
      if arch == 'unknown':
         exit(1)

   global debugOutput
   debugOutput = args.debug

   global useIACA
   if args.iaca:
      useIACA = True

      try:
         versionString = subprocess.check_output([args.iaca], stderr=subprocess.STDOUT)
      except subprocess.CalledProcessError as e:
         versionString = e.output
      global iacaVersion
      iacaVersion = re.search('\d\.\d', versionString).group(0)
      global iacaCMDLine
      iacaCMDLine = [args.iaca, '-reduceout', '-arch', arch]
      if iacaVersion == '2.1':
         iacaCMDLine.append('-64')
   else:
      useIACA = False

      resetNanoBench()

      if arch in ['ZEN+', 'ZEN2']:
         configurePFCs(['UOPS','FpuPipeAssignment.Total0', 'FpuPipeAssignment.Total1', 'FpuPipeAssignment.Total2', 'FpuPipeAssignment.Total3', 'DIV_CYCLES'])
      else:
         configurePFCs(['UOPS', 'RETIRE_SLOTS', 'UOPS_PORT0', 'UOPS_PORT1', 'UOPS_PORT2', 'UOPS_PORT3', 'UOPS_PORT4', 'UOPS_PORT5', 'UOPS_PORT6', 'UOPS_PORT7',
                        'UOPS_PORT23', 'UOPS_PORT49', 'UOPS_PORT78', 'DIV_CYCLES', 'ILD_STALL.LCP', 'INST_DECODED.DEC0'])

   try:
      subprocess.check_output('mkdir -p /tmp/ramdisk; sudo mount -t tmpfs -o size=100M none /tmp/ramdisk/', shell=True)
   except subprocess.CalledProcessError as e:
      print "Could not create ramdisk " + e.output
      exit(1)

   XMLRoot = ET.parse(args.input).getroot()
   XMLRoot.attrib['date'] = str(datetime.date.today())

   global instrNodeList
   instrNodeList = filterInstructions(XMLRoot)

   global instrNodeDict
   instrNodeDict = {instrNode.attrib['string']: instrNode for instrNode in instrNodeList}

   # move instructions that need a preInstr to the end, as their throughput can only be determined after the throughput of the instructions included in the
   # preInstr has been measured
   instrRequiringPreInstr = []
   if not useIACA:
      instrRequiringPreInstr = [x for x in instrNodeList if 'DIV' in x.attrib['iclass'] or 'SQRT' in x.attrib['iclass'] or getPreInstr(x)[0]]
   instrNodeList.sort(key=lambda x: (x in instrRequiringPreInstr, x.attrib['string']))

   for instrNode in instrNodeList:
      archNode = instrNode.find('./architecture[@name="' + arch + '"]')
      if archNode is None:
         archNode = SubElement(instrNode, "architecture")
         archNode.attrib['name'] = arch
      if not useIACA:
         measurementNode = archNode.find('./measurement')
         if measurementNode is None:
            measurementNode = SubElement(archNode, "measurement")

   ########################
   # Througput and Uops
   ########################

   tpDict = {}
   tpDictSameReg = {}
   tpDictNoInteriteration = {}

   if args.tpInput is not None:
      with open(args.tpInput, 'rb') as f:
         pTpDict, pTpDictSameReg, pTpDictNoInteriteration = pickle.load(f)
         tpDict = {instrNodeDict[k.attrib['string']]:v for k,v in pTpDict.items()}
         tpDictSameReg = {instrNodeDict[k.attrib['string']]:v for k,v in pTpDictSameReg.items()}
         tpDictNoInteriteration = {instrNodeDict[k.attrib['string']]:v for k,v in pTpDictNoInteriteration.items()}
   else:
      for i, instrNode in enumerate(instrNodeList):
         print 'Measuring throughput for ' + instrNode.attrib['string'] + ' (' + str(i) + '/' + str(len(instrNodeList)) + ')'

         htmlReports = ['<h1>' + instrNode.attrib['string'] + ' - Throughput and Uops' + (' (IACA '+iacaVersion+')' if useIACA else '') + '</h1>\n<hr>\n']

         hasCommonReg = hasCommonRegister(instrNode)
         if hasCommonReg: htmlReports.append('<h2 id="distinctRegs">With different registers for different operands</h2>\n')

         tpResult = getThroughputAndUops(instrNode, True, htmlReports)
         print instrNode.attrib['string'] + " - tp: " + str(tpResult)

         if tpResult:
            tpDict[instrNode] = tpResult

         if hasCommonReg:
            htmlReports.append('<hr><h2 id="sameReg">With the same register for for different operands</h2>\n')
            tpResultSameReg = getThroughputAndUops(instrNode, False, htmlReports)
            if tpResultSameReg:
               tpDictSameReg[instrNode] = tpResultSameReg

         if useIACA and iacaVersion in ['2.1', '2.2']:
            htmlReports.append('<hr><h2 id="noInteriteration">With the -no_interiteration flag</h2>\n')
            tp = getThroughputIacaNoInteriteration(instrNode, htmlReports)
            if tp: tpDictNoInteriteration[instrNode] = tp

         if tpResult: writeHtmlFile('html-tp/'+arch, instrNode, instrNode.attrib['string'], ''.join(htmlReports))
      with open('tp_' + arch + '.pickle', 'wb') as f:
         pickle.dump((tpDict, tpDictSameReg, tpDictNoInteriteration), f)

   num_ports = len(tpDict.values()[0].unblocked_ports)

   ########################
   # Latency
   ########################

   if not useIACA:
      configurePFCs(['UOPS'])
      getBasicLatencies(instrNodeList)

   latencyDict = {}

   if args.latInput is not None:
      with open(args.latInput, 'rb') as f:
         latencyDict = {instrNodeDict[k.attrib['string']]:v for k,v in pickle.load(f).items()}
   elif not useIACA or iacaVersion == '2.1':
      for i, instrNode in enumerate(instrNodeList):
         print 'Measuring latencies for ' + instrNode.attrib['string'] + ' (' + str(i) + '/' + str(len(instrNodeList)) + ')'

         htmlReports = ['<h1>' + instrNode.attrib['string'] + ' - Latency' + (' (IACA '+iacaVersion+')' if useIACA else '') + '</h1>\n<hr>\n']
         lat = getLatencies(instrNode, instrNodeList, tpDict, htmlReports)

         if lat is not None:
            if debugOutput: print instrNode.attrib['iform'] + ': ' + str(lat)
            latencyDict[instrNode] = lat
            writeHtmlFile('html-lat/'+arch, instrNode, instrNode.attrib['string'], ''.join(htmlReports))
      with open('lat_' + arch + '.pickle', 'wb') as f:
          pickle.dump(latencyDict, f)

   ########################
   # Ports
   ########################

   if not useIACA:
      configurePFCs(['UOPS'])

   # the elements of this set are sets of ports that either have the same functional units, or that cannot be used independently
   portCombinationsResultDict = {}
   portCombinationsResultDictSameReg = {}

   if not args.noPorts:
      # iforms of instructions that are potentially zero-latency instructions
      # we consider all MOVZX instructions to be potentially zero-latency instr.; the descr. in the manual is not accurate as, e.g., MOVZX RSI, CL can be
      # eliminated, but MOVZX RSI, DIL cannot (at least on Coffee Lake)
      zeroLatencyMovIforms = set(x.attrib['iform'] for x in instrNodeList
                                    if x.attrib['iform'].startswith(('MOV_', 'MOVZX_', 'NOP', 'MOVUPD_', 'MOVAPD_', 'MOVUPS_', 'MOVAPS_', 'MOVDQA_', 'MOVDQU_',
                                                                     'VMOVUPD_', 'VMOVAPD_', 'VMOVUPS_', 'VMOVAPS_', 'VMOVDQA_', 'VMOVDQU_'))
                                       and len(x.findall('./operand[@type="reg"]')) >= 2 and not 'MEM' in x.attrib['iform'])
      # iforms of instructions that change the control flow based on a register, flag, or memory location
      controlFlowInstrs = set(instr for instr in instrNodeList for op in instr.findall('./operand[@type="reg"]') if op.text == 'RIP')
      disallowedBlockingInstrs = set(instr for instr in tpDict
                                     if instr.attrib['iform'] in (zeroLatencyMovIforms | serializingInstructions | set(['PAUSE']))
                                        or (instr in controlFlowInstrs and not instr.attrib['iform'] == 'JMP_RELBRb')
                                        or (instr.find('./operand[@base="RSP"]') is not None)
                                        or (instr.find('./operand[@conditionalWrite="1"]') is not None)
                                        or instr.attrib['category'] == 'SYSTEM'
                                        or instr.attrib['extension'] == 'X87'
                                        or '_AL_' in instr.attrib['iform'] or '_OrAX_' in instr.attrib['iform']
                                        or tpDict[instr].TP_noDepBreaking_noLoop - .2 > max([uops for _, uops in tpDict[instr].unblocked_ports.items()] or [0])
                                        or '512' in instr.attrib['isa-set']) # on SKX, some AVX-512 instructions can 'shut down' vector units on port 1
      if isAMDCPU():
         disallowedBlockingInstrs |= set(instr for instr in instrNodeList for op in instr.findall('./operand[@type="mem"]'))
         # combining SHA instr. with other instr. leads to wrong port counts
         disallowedBlockingInstrs |= set(instr for instr in instrNodeList if instr.attrib['extension'] == 'SHA')
         # combining FP with non-FP instr. can lead to wrong port counts
         disallowedBlockingInstrs |= set(instr for instr in instrNodeList if instr.attrib['category'] in ['LOGICAL_FP'] or
                                         any(not 'f' in o.attrib.get('xtype','f') for o in instr.findall('./operand')))

      print 'disallowedBlockingInstrs'
      for instrNode in disallowedBlockingInstrs:
         print '  ' + str(instrNode.attrib['string'])

      print 'tpDict'
      for instr, tpResult in tpDict.items():
         print '  ' + str(instr.attrib['string']) + ' ' + str(tpResult.unblocked_ports)

      # we cannot start higher than .79 as IACA has .2 uops on each port for a port usage of, e.g., 1*p1256
      # using uops_dict instead can be problematic because in IACA the uops on the individual ports do not always add up to this value
      oneUopInstrs = [instr for instr, tpResult in tpDict.items() if instr not in disallowedBlockingInstrs and .79 < sum([v for v in tpResult.unblocked_ports.values() if v>.1]) < 1.11]

      print 'oneUopInstrs'
      for instrNode in oneUopInstrs:
         print '  ' + str(instrNode.attrib['string'])
      # dicts from port combination to a set of instructions (either not containing AVX or SSE instructions bec. of transition penalty) that always uses these ports
      blockingInstructionsDictNonAVX_set = {}
      blockingInstructionsDictNonSSE_set = {}

      for instrNode in oneUopInstrs:
         usedPorts = frozenset({p for p, x in tpDict[instrNode].unblocked_ports.items() if x>0.1})
         if usedPorts:
            print instrNode.attrib['iform'] + ': ' + str(usedPorts) + ' ' + str(len(instrNode.findall('./operand[@suppressed="1"]')))

            if not isSSEInstr(instrNode):
               if not usedPorts in blockingInstructionsDictNonSSE_set: blockingInstructionsDictNonSSE_set[usedPorts] = set()
               blockingInstructionsDictNonSSE_set[usedPorts].add(instrNode)
            if not isAVXInstr(instrNode):
               if not usedPorts in blockingInstructionsDictNonAVX_set: blockingInstructionsDictNonAVX_set[usedPorts] = set()
               blockingInstructionsDictNonAVX_set[usedPorts].add(instrNode)

      # choose instruction with lowest throughput value; prefer non-control flow instructions, instr. that do not need decoder 0, and instr. with as few as
      # possible implicit operands that are read
      sort_key = lambda x:(x in controlFlowInstrs, tpDict[x].dec0, len(x.findall('./operand[@suppressed="1"]')), tpDict[x].TP_noDepBreaking_noLoop, x.attrib['string'])
      blockingInstructionsDictNonAVX = {comb: next(iter(sorted(instr_set, key=sort_key))) for comb, instr_set in blockingInstructionsDictNonAVX_set.items()}
      blockingInstructionsDictNonSSE = {comb: next(iter(sorted(instr_set, key=sort_key))) for comb, instr_set in blockingInstructionsDictNonSSE_set.items()}

      for comb, instr_set in blockingInstructionsDictNonAVX_set.items():
         print comb
         print [x.attrib['string'] for x in sorted(instr_set, key=sort_key)]

      print str(blockingInstructionsDictNonAVX.items())

      # mov to mem has always two uops: store address and store data; there is no instruction that uses just one of them
      movMemInstrNode = instrNodeDict['MOV (M64, R64)']

      if arch in ['ICL']:
         storeDataPort = 49
      else:
         storeDataPort = 4
      blockingInstructionsDictNonAVX[frozenset({storeDataPort})] = movMemInstrNode
      blockingInstructionsDictNonSSE[frozenset({storeDataPort})] = movMemInstrNode

      storeAddressPorts = frozenset({p for p, x in tpDict[movMemInstrNode].unblocked_ports.items() if x>=0.1 and not p == storeDataPort})
      if storeAddressPorts not in blockingInstructionsDictNonAVX: blockingInstructionsDictNonAVX[storeAddressPorts] = movMemInstrNode
      if storeAddressPorts not in blockingInstructionsDictNonSSE: blockingInstructionsDictNonSSE[storeAddressPorts] = movMemInstrNode

      print 'Non-AVX:'
      for k,v in blockingInstructionsDictNonAVX.items():
         print str(k) + ': ' + v.attrib['iform']
      print 'Non-SSE:'
      for k,v in blockingInstructionsDictNonSSE.items():
         print str(k) + ': ' + v.attrib['iform']

      sortedPortCombinationsNonAVX = sorted(blockingInstructionsDictNonAVX.keys(), key=lambda x:(len(x), sorted(x)))
      sortedPortCombinationsNonSSE = sorted(blockingInstructionsDictNonSSE.keys(), key=lambda x:(len(x), sorted(x)))
      print 'sortedPortCombinations: ' + str(sortedPortCombinationsNonAVX)

      for i, instrNode in enumerate(instrNodeList):
         if not instrNode in tpDict:
            # don't iterate over the keys of unblocked_ports_dict directly because of the ordering
            continue

         print 'Measuring port usage for ' + instrNode.attrib['string'] + ' (' + str(i) + '/' + str(len(instrNodeList)) + ')'

         htmlReports = ['<h1>' + instrNode.attrib['string'] + ' - Port Usage' + (' (IACA '+iacaVersion+')' if useIACA else '') + '</h1>']

         for useDistinctRegs in ([True, False] if instrNode in tpDictSameReg else [True]):

            tpResult = None

            if useDistinctRegs:
               tpResult = tpDict[instrNode]
            else:
               if tpDict[instrNode].uops == tpDictSameReg[instrNode].uops: continue
               tpResult = tpDictSameReg[instrNode]
               htmlReports.append('<h2>With the same register for different operands</h2>')

            rem_uops = max(tpResult.uops, int(sum(x for p, x in tpResult.unblocked_ports.items() if x>0) + .2))

            if not useIACA and tpResult.config.preInstrNodes:
               rem_uops -= sum(tpDict[instrNodeDict[preInstrNode.attrib['string']]].uops for preInstrNode in tpResult.config.preInstrNodes)

            # use abs because on, e.g., IVB port usages might be smaller in the second half of the experiments if replays happen
            used_ports = {p for p, x in tpResult.unblocked_ports.items() if abs(x)>0.05}
            if debugOutput: print instrNode.attrib['string'] + ' - used ports: ' + str(used_ports) + ', dict: ' + str(tpResult.unblocked_ports)

            if not isAVXInstr(instrNode):
               blockingInstrs = blockingInstructionsDictNonAVX
               sortedPortCombinations = sortedPortCombinationsNonAVX
            else:
               blockingInstrs = blockingInstructionsDictNonSSE
               sortedPortCombinations = sortedPortCombinationsNonSSE

            uopsCombinationList = []

            if not used_ports:
               htmlReports.append('No uops')
            elif (rem_uops == 1) and (not tpResult.config.preInstrNodes) and (not tpResult.ILD_stalls > 0):
               # one uop instruction
               uopsCombinationList = [(frozenset(used_ports), 1)]
               htmlReports.append('<hr>Port usage: 1*' + ('p' if isIntelCPU() else 'FP') + ''.join(str(p) for p in used_ports))
            elif rem_uops > 0 and not isAMDCPU():
               for combination in sortedPortCombinations:
                  if not combination.intersection(used_ports): continue

                  prevUopsOnCombination = 0
                  for prev_combination, prev_uops in uopsCombinationList:
                     if prev_combination.issubset(combination):
                        prevUopsOnCombination += prev_uops

                  if not useIACA:
                     if tpResult.config.preInstrNodes:
                        for preInstrNode in tpResult.config.preInstrNodes:
                           for pre_comb, pre_uops in portCombinationsResultDict[instrNodeDict[preInstrNode.attrib['string']]]:
                              if pre_comb.issubset(combination):
                                 prevUopsOnCombination += pre_uops

                  nPortsInComb = sum(len(str(x)) for x in combination)
                  blockInstrRep = max(2 * nPortsInComb * max(1,int(tpDict[instrNode].TP_single)), nPortsInComb * tpDict[instrNode].uops, 10)
                  blockInstrRep = min(blockInstrRep, 100)
                  uopsOnBlockedPorts = getUopsOnBlockedPorts(instrNode, useDistinctRegs, blockingInstrs[combination], blockInstrRep, combination, tpResult.config, htmlReports)
                  if uopsOnBlockedPorts is None:
                     print 'no uops on blocked ports: ' + str(combination)
                     continue

                  uopsOnBlockedPorts -= prevUopsOnCombination

                  if rem_uops < uopsOnBlockedPorts:
                     print 'More uops on ports than total uops, combination: ' + str(combination) + ', ' + str(uopsOnBlockedPorts)

                  if uopsOnBlockedPorts <= 0: continue

                  if combination == {storeDataPort} and instrNode.attrib.get('locked', '') == '1':
                     # for instructions with a lock prefix, the blocking instrs don't seem to be sufficient for actually blocking the store data port, which
                     # seems to lead to replays of the store data uops
                     uopsOnBlockedPorts = 1

                  uopsCombinationList.append((combination, uopsOnBlockedPorts))

                  htmlReports.append('<strong>&#8680; ' +
                                     ((str(uopsOnBlockedPorts) + ' &mu;ops') if (uopsOnBlockedPorts > 1) else 'One &mu;op') +
                                     ' that can only use port' +
                                     ('s {' if len(combination)>1 else ' ') +
                                     str(list(combination))[1:-1] +
                                     ('}' if len(combination)>1 else '') + '</strong>')

                  rem_uops -= uopsOnBlockedPorts
                  if rem_uops <= 0: break

            # on ICL, some combinations (e.g. {4,9}) are treated as one port (49) above, as there is only a single counter for both ports
            # we split these combinations now, as, e.g., the call to getTP_LP requires them to be separate
            uopsCombinationList = [(frozenset(''.join(map(str,comb))), uops) for comb, uops in uopsCombinationList]

            if useDistinctRegs:
               portCombinationsResultDict[instrNode] = uopsCombinationList
            else:
               portCombinationsResultDictSameReg[instrNode] = uopsCombinationList

         writeHtmlFile('html-ports/'+arch, instrNode, instrNode.attrib['string'], ''.join(htmlReports))


   ########################
   # Write XML File
   ########################

   for instrNode in tpDict:
      archNode = instrNode.find('./architecture[@name="' + arch + '"]')
      if useIACA:
         resultNode = SubElement(archNode, "IACA")
         resultNode.attrib['version'] = iacaVersion
      else:
         resultNode = archNode.find('./measurement')

      tpResult_dr = tpDict[instrNode]
      tpResult_sr = tpDictSameReg.get(instrNode, tpResult_dr)

      for tpResult in ([tpResult_dr, tpResult_sr] if tpResult_dr.uops != tpResult_sr.uops else [tpResult_dr]):
         suffix = ('' if tpResult == tpResult_dr else '_same_reg')
         curPortCombinationsResultDict = (portCombinationsResultDict if tpResult == tpResult_dr else portCombinationsResultDictSameReg)

         uops = tpResult.uops
         uopsFused = tpResult.fused_uops
         if useIACA:
            if uopsFused:
               resultNode.attrib['fusion_occurred'] = '1'
         else:
            if tpResult.config.preInstrNodes:
               uops -= sum(tpDict[instrNodeDict[preInstrNode.attrib['string']]].uops for preInstrNode in tpResult.config.preInstrNodes)
               uopsFused -= sum(tpDict[instrNodeDict[preInstrNode.attrib['string']]].fused_uops for preInstrNode in tpResult.config.preInstrNodes)
            if uopsFused > 0:
               resultNode.attrib['uops_retire_slots'+suffix] = str(uopsFused)
         resultNode.attrib['uops'+suffix] = str(uops)

         if useIACA and instrNode in latencyDict:
            resultNode.attrib['latency'] = str(latencyDict[instrNode])

         resultNode.attrib['TP'+suffix] = "%.2f" % tpResult.TP
         if instrNode in tpDictNoInteriteration:
            resultNode.attrib['TP_no_interiteration'] = "%.2f" % tpDictNoInteriteration[instrNode]

         divCycles = tpResult.divCycles
         if divCycles: resultNode.attrib['div_cycles'+suffix] = str(divCycles)

         portPrefix = ('p' if isIntelCPU() else 'FP')
         computePortStr = lambda lst: '+'.join(str(uops)+'*'+portPrefix+''.join(str(p) for p in sorted(c)) for c, uops in sorted(lst, key=lambda x: sorted(x[0])))
         if instrNode in curPortCombinationsResultDict:
            portUsageList = curPortCombinationsResultDict[instrNode]
            if not portUsageList: continue

            resultNode.attrib['ports'+suffix] = computePortStr(portUsageList)

            portUsageWithDivList = list(portUsageList)
            if divCycles:
               portUsageWithDivList.append((frozenset(['div']), divCycles))
            resultNode.attrib['TP_ports'+suffix] = "%.2f" % getTP_LP(portUsageWithDivList)

   with open(args.output, "w") as f:
      reparsed = XMLRoot
      if not args.noPretty:
         rough_string = ET.tostring(XMLRoot, 'utf-8')
         reparsed = minidom.parseString(rough_string)
      f.write('\n'.join([line for line in reparsed.toprettyxml(indent='  ').split('\n') if line.strip()]))

   with tarfile.open('genhtml-' + arch + (('-IACA' + iacaVersion) if useIACA else '-Measurements') + '.tar.gz', "w:gz") as tar:
      tar.add('/tmp/cpu-html/', arcname=os.path.sep)

   shutil.rmtree('/tmp/cpu-html/')

   try:
      subprocess.check_output('umount /tmp/ramdisk/', shell=True)
   except subprocess.CalledProcessError:
      exit(1)

   print 'Total number of microbenchmarks: ' + str(nExperiments)


if __name__ == "__main__":
    main()
