// CubeSetlistRemote driver for Cubase (official MIDI Remote API).
// Dumb driver: 1 CC = 1 command; sequences in Python (mr_ctrl.py).
// CC-TABLE: start=102 stop=103 rtz=104 save=105 close=106

var midiremote_api = require('midiremote_api_v1')

var deviceDriver = midiremote_api.makeDeviceDriver(
    'CubeSetlistManager',
    'CubeSetlistRemote',
    'Kaizhou Xie')

var midiInput = deviceDriver.mPorts.makeMidiInput()
var midiOutput = deviceDriver.mPorts.makeMidiOutput()

deviceDriver.makeDetectionUnit().detectPortPair(midiInput, midiOutput)
    .expectInputNameEquals('CubeSetlist Remote')
    .expectOutputNameEquals('CubeSetlist Remote')

var bStart = deviceDriver.mSurface.makeButton(0, 0, 2, 1)
var bStop = deviceDriver.mSurface.makeButton(2, 0, 2, 1)
var bRtz = deviceDriver.mSurface.makeButton(4, 0, 2, 1)
var bSave = deviceDriver.mSurface.makeButton(6, 0, 2, 1)
var bClose = deviceDriver.mSurface.makeButton(8, 0, 2, 1)

function bindCC(btn, cc) {
    btn.mSurfaceValue.mMidiBinding
        .setInputPort(midiInput)
        .setOutputPort(midiOutput)
        .bindToControlChange(0, cc)
}

bindCC(bStart, 102)
bindCC(bStop, 103)
bindCC(bRtz, 104)
bindCC(bSave, 105)
bindCC(bClose, 106)

var page = deviceDriver.mMapping.makePage('Setlist Control')

page.makeCommandBinding(bStart.mSurfaceValue, 'Transport', 'Start')
page.makeCommandBinding(bStop.mSurfaceValue, 'Transport', 'Stop')
page.makeCommandBinding(bRtz.mSurfaceValue, 'Transport', 'Return to Zero')
page.makeCommandBinding(bSave.mSurfaceValue, 'File', 'Save')
page.makeCommandBinding(bClose.mSurfaceValue, 'File', 'Close')