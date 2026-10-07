"""Generates the Blueprint paste text (T3D) for the DeclineSupplyShipDefault mod into tools/out/.
Usage: python tools/declinesupplyshipdefault_build.py   (needs the modkit's jmap, see t3d.py)

What the mod does: when the Claws' supply ship docks ("Choose a supply crate"), the selected choice is
switched to "Reject Shipment", so ignoring the ship or clicking accept by habit doesn't add new debt.
The player can still pick any crate in the window.

How (game 0.7.207, UE 5.8; found in the reflection dump + exe):
  - ArcoSystems.GetArcoSys() -> GetPrimaryDockActor() -> FactionDock component. Its multicast delegate
    DockedShipChanged(shipTypeAtDock: EShipType) fires when a ship arrives at / leaves the main dock.
  - ArcoSystems.GetCurrentlyDockedShipInfo() -> DockableShipData.asSupplyShip (SupplyShipState):
    choices (SupplyShipChoice array: Resources, Cost, isRejectChoice, rejectionCost) + choiceIdx (selected).
  - The supply window class DockedBoatPanel_Supplies handles HudAction "chooseSupplyPackage" itself
    (exe 0x144baf320, its ReceiveHudAction): paramInt = choice index -> sim action SHIP_SUPPLY_CHOOSE_IDX.
    "accept" then applies choices[choiceIdx] (isRejectChoice -> "Supplies rejected!").
    We send it through ReceiveHudAction on one invisible DockedBoatPanel_Supplies (created once per map).
  - Only acts on a ship that just docked (the delegate), never at load; if the ship's choices aren't filled
    yet, re-checks with a one-shot timer (max 3 tries).

Assets (/Game/Mods/DeclineSupplyShipDefault/): BP_Startup (Actor), BP_MapLoad (Actor), PAL_DeclineSupplyShipDefault.
Debug log needs %localappdata%\\Whiskerwood\\Saved\\mods\\DeclineSupplyShipDefaultConfig\\debug.txt (any text).
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from t3d import *

OUT = os.path.join(os.path.dirname(__file__), 'out')
os.makedirs(OUT, exist_ok=True)
MOD = 'DeclineSupplyShipDefault'
VERSION = '1.0'
BUILD = 'v8'
TRACE = False   # True = ungated log lines (dev only, never in a release)
DIAG = False    # True = v4-v7 diagnostics (panel pass-up log, panel class, 1 s re-read). Dev only.
M = '/Game/Mods/%s/' % MOD
STARTUP, MAPLOAD = M + 'BP_Startup', M + 'BP_MapLoad'
KSL = '/Script/Engine.KismetSystemLibrary'
KML = '/Script/Engine.KismetMathLibrary'
KSTR = '/Script/Engine.KismetStringLibrary'
KAL = '/Script/Engine.KismetArrayLibrary'
GS = '/Script/Engine.GameplayStatics'
API = '/Script/SystemCore.ModAPI'
PA = '/Script/ProjectArco.'
# v7: the crate window (ship type 3) is DockedBoatPanel_SupplyTrader. DockedBoatPanel_Supplies is the STARTING-supplies
# panel (BP_DockedBoatUI_Supplies: 'Your starting supplies have arrived', asEmbarkSupplies) and passes chooseSupplyPackage up.
SYS, FDOCK, PANEL = PA + 'ArcoSystems', PA + 'FactionDock', PA + 'DockedBoatPanel_SupplyTrader'
SHIPDATA, SSTATE, SCHOICE = PA + 'DockableShipData', PA + 'SupplyShipState', PA + 'SupplyShipChoice'
OPT = 'DeclineSupplyShipDefault_On'
NO_SHIP = '6'   # EShipType::NO_SHIP
CRATE_SHIP = '3'   # EShipType::SUPPLY_SHIP_TRADE = the 'Choose a supply crate' ship
TRIES = '3'
RETRY_S = '1.0'
FIRST_S = '0.5'   # game seconds after the ship docked
ACTOR = OBJ('/Script/Engine.Actor')
SYS_T, FDOCK_T, PANEL_T = OBJ(SYS), OBJ(FDOCK), OBJ(PANEL)
CHOICE_T = STRUCT(SCHOICE)

# ---- PinSubCategoryMemberReference support (delegate pins)
_orig_fmt = Pin.fmt
def _fmt(self):
    s = _orig_fmt(self)
    mr = getattr(self, 'memref', None)
    if mr: s = s.replace('PinType.PinSubCategoryMemberReference=()', 'PinType.PinSubCategoryMemberReference=(%s)' % mr, 1)
    return s
Pin.fmt = _fmt


def modapi(g, x, y, name='Api'):
    return g.call(API + ':GetModAPI', name, x, y)


def api_call(g, fn, x, y, name=None, **kw):
    a = modapi(g, x - 250, y + 150, name=(name or fn) + 'Api')
    n = g.call(API + ':' + fn, name or fn, x, y, **kw)
    link(a['ReturnValue'], n['self'])
    return n


class _Gate(Node):
    def __init__(self, entry, exit_):
        self._e, self._x = entry, exit_; self.name = entry.node.name
    def __getitem__(self, k):
        return {'execute': self._e, 'then': self._x}[k]


def log(g, x, y, msg_pin=None, msg=None, name='Log'):
    """Debug-only log line (Debug = <Mod>Config\\debug.txt not empty), joined back by a reroute knot."""
    dg = g.get('Debug', BOOL, x - 200, y + 120, name=name + 'DebugGet')
    br = g.branch(x - 150, y, name + 'IfDebug'); link(dg['Debug'], br['Condition'])
    n = api_call(g, 'LogMessage', x, y, name=name, doPrependDate='true')
    if msg_pin: link(msg_pin, n['Msg'])
    elif msg: n.set('Msg', msg)
    ex(br, n)
    k = g.add(BG + 'K2Node_Knot', name + 'Join', [], x + 250, y - 40)
    k.pin('InputPin', EXEC); k.pin('OutputPin', EXEC, out=True)
    link(n['then'], k['InputPin']); link(br['else'], k['InputPin'])
    return _Gate(br['execute'], k['OutputPin'])


def concat(g, x, y, *parts):
    cur = None
    for i, p in enumerate(parts):
        if cur is None:
            if isinstance(p, str):
                c = g.call(KSTR + ':Concat_StrStr', 'Cat', x, y); c.set('A', p); cur = c['ReturnValue']; continue
            cur = p; continue
        c = g.call(KSTR + ':Concat_StrStr', 'Cat', x + 40 * i, y + 30 * i)
        link(cur, c['A'])
        if isinstance(p, str): c.set('B', p)
        else: link(p, c['B'])
        cur = c['ReturnValue']
    return cur


def istr(g, pin, x, y, name):
    n = g.call(KSTR + ':Conv_IntToString', name, x, y); link(pin, n['inInt']); return n['ReturnValue']


def bstr(g, pin, x, y, name):
    n = g.call(KSTR + ':Conv_BoolToString', name, x, y); link(pin, n['InBool']); return n['ReturnValue']


def nstr(g, pin, x, y, name):
    n = g.call(KSTR + ':Conv_NameToString', name, x, y); link(pin, n['InName']); return n['ReturnValue']


def is_valid(g, pin, x, y, name='Valid'):
    n = g.call(KSL + ':IsValid', name, x, y); link(pin, n['Object']); return n['ReturnValue']


def struct_node(g, kind, spath, show, x, y, name):
    props = J()[spath]['properties']
    n = g.add(BG + 'K2Node_%sStruct' % kind, name,
              ['StructType="/Script/CoreUObject.ScriptStruct\'%s\'"' % spath, 'bMadeAfterOverridePinRemoval=True'], x, y)
    for i, p in enumerate(props):
        n.header.append('ShowPinForProperties(%d)=(PropertyName="%s",bShowPin=%s,bCanToggleVisibility=True)' % (i, p['name'], p['name'] in show))
    sname = spath.split('.')[-1]
    if kind == 'Break':
        n.pin(sname, STRUCT(spath))
    for p in props:
        if p['name'] in show:
            d = None
            if kind == 'Make':
                d = {'IntProperty': '0', 'StrProperty': '', 'BoolProperty': 'false'}.get(p['type'])
            n.pin(p['name'], prop_type(p), out=(kind == 'Break'), default=d)
    if kind == 'Make':
        n.pin(sname, STRUCT(spath), out=True)
    return n


def bind(g, target_pin, owner, delegate, sig_pkg, sig, event_node, x, y, name):
    n = g.add(BG + 'K2Node_AddDelegate', name,
              ['DelegateReference=(MemberParent="%s",MemberName="%s")' % (cls_ref(owner), delegate)], x, y)
    n.pin('execute', EXEC); n.pin('then', EXEC, out=True)
    n.pin('self', OBJ(owner), friendly='NSLOCTEXT("K2Node", "Target", "Target")')
    d = n.pin('Delegate', T('delegate'))
    d.memref = 'MemberParent="/Script/CoreUObject.Package\'%s\'",MemberName="%s"' % (sig_pkg, sig)
    link(target_pin, n['self'])
    ev = event_node['OutputDelegate']
    ev.memref = 'MemberParent="/Script/Engine.BlueprintGeneratedClass\'%s.%s_C\'",MemberName="%s"' % (MAPLOAD, MAPLOAD.split('/')[-1], event_node.name)
    link(ev, d)
    return n


def arr_fn(g, fn, elem_t, x, y, name):
    n = g.add(BG + 'K2Node_CallArrayFunction', name,
              ['FunctionReference=(MemberParent="%s",MemberName="%s")' % (cls_ref(KAL), fn)], x, y)
    n.pin('execute', EXEC); n.pin('then', EXEC, out=True)
    n.pin('self', OBJ(KAL), defobj='/Script/Engine.Default__KismetArrayLibrary', hidden=True, friendly='NSLOCTEXT("K2Node", "Target", "Target")')
    at = ARR(elem_t); at['ref'] = True
    n.pin('TargetArray', at)
    it = dict(elem_t); it['ref'] = True; it['const'] = True
    if fn == 'Array_Add':
        n.pin('NewItem', it); n.pin('ReturnValue', INT, out=True)
    return n


def array_item(g, arr_pin, elem_t, idx_pin, x, y, name):
    n = g.add(BG + 'K2Node_GetArrayItem', name, ['bReturnByRefDesired=False'], x, y)
    at = ARR(elem_t); at['ref'] = True; at['const'] = True
    n.pin('Array', at); n.pin('Dimension 1', INT, default='0'); n.pin('Output', elem_t, out=True)
    link(arr_pin, n['Array']); link(idx_pin, n['Dimension 1'])
    return n['Output']


def class_cast(g, x, y, name):
    n = g.add(BG + 'K2Node_ClassDynamicCast', name, ['TargetType="%s"' % cls_ref('/Script/Engine.Actor')], x, y)
    n.pin('execute', EXEC); n.pin('then', EXEC, out=True); n.pin('CastFailed', EXEC, out=True)
    n.pin('Class', CLS('/Script/CoreUObject.Object'))
    n.pin('AsActor', ACLS, out=True)
    n.pin('bSuccess', BOOL, out=True, hidden=True)
    return n


def knot(g, x, y, name):
    k = g.add(BG + 'K2Node_Knot', name, [], x, y)
    k.pin('InputPin', EXEC); k.pin('OutputPin', EXEC, out=True)
    return k



def trace(g, x, y, msg=None, msg_pin=None, name='Trace'):
    """TEMP ungated line (only when TRACE). Returns a gate-like node or None."""
    if not TRACE: return None
    n = api_call(g, 'LogMessage', x, y, name=name, doPrependDate='true')
    if msg_pin: link(msg_pin, n['Msg'])
    else: n.set('Msg', msg)
    return n


def selfn(g, x, y, name):
    n = g.add(BG + 'K2Node_Self', name, [], x, y); n.pin('self', T('object', sub='self'), out=True); return n


def gname(g, pin, x, y, name):
    n = g.call(KSL + ':GetDisplayName', name, x, y); link(pin, n['Object']); return n['ReturnValue']


def bystr(g, pin, x, y, name):
    n = g.call(KSTR + ':Conv_ByteToString', name, x, y); link(pin, n['InByte']); return n['ReturnValue']


# =========================================================================== BP_Startup
g = Graph(STARTUP)
bp = g.event('/Script/Engine.Actor', 'ReceiveBeginPlay', [], 'Begin', 0, 0)
vals = g.add(BG + 'K2Node_MakeArray', 'Vals', ['NumInputs=2'], 300, 250)
vals.pin('Array', ARR(STR), out=True)
vals.pin('[0]', STR, default='On'); vals.pin('[1]', STR, default='Off')
reg = api_call(g, 'RegisterModOptions', 600, 0, name='Reg', optionId=OPT,
               optionDisplayName='Supply ship - Refuse supplies selected by default', DefaultValue='On',
               optionDescription='When the supply ship docks, Refuse supplies is selected instead of a supply crate, so no new debt is added unless you pick a crate yourself.')
link(vals['Array'], reg['Values']); ex(bp, reg)
open(OUT + '/BP_Startup.txt', 'w', encoding='utf-8').write(g.text())

# =========================================================================== BP_MapLoad
# Variables: Debug (Boolean), Sys (Arco Systems ref), Panel (Docked Boat Panel Supplies ref),
#            Choices (Supply Ship Choice ARRAY), CurIdx (Integer), RejectIdx (Integer), Tries (Integer),
#            RetryPending (Boolean), ShipType (Integer)
g = Graph(MAPLOAD)

# ---- BeginPlay: bind onLoadingFinished
bp = g.event('/Script/Engine.Actor', 'ReceiveBeginPlay', [], 'Begin', 0, -2000)
api = modapi(g, 200, -1750, name='BindApi')
evL = g.custom_event('OnLoaded', [], 0, -1400)
b0 = bind(g, api['ReturnValue'], API, 'onLoadingFinished', '/Script/SystemCore', 'ModAPI_OnEvent__DelegateSignature', evL, 400, -2000, 'BindLoaded')
t0 = trace(g, 200, -2300, msg='%s TRACE %s: BP_MapLoad BeginPlay' % (MOD, BUILD), name='TraceBeginPlay')
if t0: ex(bp, t0); ex(t0, b0)
else: ex(bp, b0)

# ---- OnLoaded: Debug from debug.txt; Sys; FactionDock.DockedShipChanged -> OnShipChanged; invisible panel
evS = g.custom_event('OnShipChanged', [('shipTypeAtDock', ENUM(PA + 'EShipType'))], 0, 600)
rd = api_call(g, 'ReadModTextFile', 300, -1400, name='ReadDebugFile', modName=MOD + 'Config', Filename='debug')
ex(evL, rd)
dfe = g.call(KSTR + ':IsEmpty', 'DebugFileEmpty', 550, -1250); link(rd['ReturnValue'], dfe['InString'])
dfn = g.call(KML + ':Not_PreBool', 'DebugFileThere', 750, -1250); link(dfe['ReturnValue'], dfn['A'])
sdb = g.setv('Debug', BOOL, 800, -1400, name='SetDebug'); link(dfn['ReturnValue'], sdb['Debug'])
prev = rd
tl = None
if TRACE:
    tl = trace(g, 600, -1700, msg_pin=concat(g, 600, -1850, '%s TRACE %s: OnLoaded, debug.txt length ' % (MOD, BUILD),
                                               istr(g, (lambda n_: (link(rd['ReturnValue'], n_['S']), n_['ReturnValue'])[1])(g.call(KSTR + ':Len', 'DebugLen', 400, -1900)), 500, -1900, 'DebugLenStr')), name='TraceLoaded')
if tl: ex(rd, tl); ex(tl, sdb)
else: ex(rd, sdb)
ga = g.call(SYS + ':GetArcoSys', 'GetSys', 1050, -1400); ex(sdb, ga)
ssy = g.setv('Sys', SYS_T, 1300, -1400, name='SetSys'); link(ga['ReturnValue'], ssy['Sys']); ex(ga, ssy)
pda = g.call(SYS + ':GetPrimaryDockActor', 'MainDock', 1300, -1150); link(ga['ReturnValue'], pda['self'])
gfd = g.call('/Script/Engine.Actor:GetComponentByClass', 'FactionDockComp', 1550, -1150, ComponentClass=FDOCK)
link(pda['ReturnValue'], gfd['self']); gfd['ReturnValue'].t = FDOCK_T
bfd = g.branch(1600, -1400, 'BrDock'); link(is_valid(g, gfd['ReturnValue'], 1600, -950, 'DockValid'), bfd['Condition']); ex(ssy, bfd)
bsc = bind(g, gfd['ReturnValue'], FDOCK, 'DockedShipChanged', '/Script/ProjectArco', 'ShipAtDockChanged__DelegateSignature', evS, 1850, -1400, 'BindShipChanged')
ex(bfd, bsc)
lok = log(g, 2150, -1400, msg='%s %s (%s): main dock found, waiting for ships' % (MOD, VERSION, BUILD), name='LogBound'); ex(bsc, lok)
dn = g.call(KSL + ':GetDisplayName', 'MainDockName', 1700, -800); link(pda['ReturnValue'], dn['Object'])
mno = concat(g, 1900, -800, '%s %s (%s): NO FactionDock found, main dock actor = ' % (MOD, VERSION, BUILD), dn['ReturnValue'])
lno = log(g, 1850, -1150, msg_pin=mno, name='LogNoDock'); ex(bfd, lno, 'else')
tn = trace(g, 1850, -650, msg_pin=mno, name='TraceNoDock')
jd = knot(g, 2500, -1500, 'AfterBind'); link(lok['then'], jd['InputPin'])
if tn: ex(lno, tn); link(tn['then'], jd['InputPin'])
else: link(lno['then'], jd['InputPin'])
pc = g.call(GS + ':GetPlayerController', 'PC', 2550, -1200)
cw = g.add('/Script/UMGEditor.K2Node_CreateWidget', 'CreatePanel', [], 2750, -1400)
cw.pin('execute', EXEC); cw.pin('then', EXEC, out=True)
cw.pin('Class', T('class', obj=cls_ref('/Script/UMG.UserWidget')), defobj=PANEL)
cw.pin('OwningPlayer', OBJ('/Script/Engine.PlayerController'))
cw.pin('ReturnValue', PANEL_T, out=True)
link(pc['ReturnValue'], cw['OwningPlayer']); link(jd['OutputPin'], cw['execute'])
spn = g.setv('Panel', PANEL_T, 3050, -1400, name='SetPanel'); link(cw['ReturnValue'], spn['Panel']); ex(cw, spn)
if DIAG:
    # v4 DIAG: if the panel doesn't handle an action, its ReceiveHudAction passes it up through OnHudActionDelegate
    evP = g.custom_event('OnPanelAction', [('HudAction', STRUCT(PA + 'HudAction'))], 0, 4200)
    bpa = g.add(BG + 'K2Node_AddDelegate', 'BindPanelAction', ['DelegateReference=(MemberParent="%s",MemberName="OnHudActionDelegate")' % cls_ref(PA + 'ArcoWidgetBase')], 3300, -1400)
    bpa.pin('execute', EXEC); bpa.pin('then', EXEC, out=True)
    bpa.pin('self', OBJ(PA + 'ArcoWidgetBase'), friendly='NSLOCTEXT("K2Node", "Target", "Target")')
    dpin = bpa.pin('Delegate', T('delegate'))
    dpin.memref = 'MemberParent="/Script/CoreUObject.Package\'/Script/ProjectArco\'",MemberName="OnHudAction__DelegateSignature"'
    link(cw['ReturnValue'], bpa['self'])
    evo = evP['OutputDelegate']; evo.memref = 'MemberParent="/Script/Engine.BlueprintGeneratedClass\'%s.%s_C\'",MemberName="OnPanelAction"' % (MAPLOAD, MAPLOAD.split('/')[-1])
    link(evo, dpin); ex(spn, bpa)
    bha = struct_node(g, 'Break', PA + 'HudAction', ['action', 'paramInt'], 200, 4500, 'BreakPanelAction'); link(evP['HudAction'], bha['HudAction'])
    mpa = concat(g, 400, 4500, '%s: DIAG panel passed the action up (not handled): ' % MOD, bha['action'], ' int ', istr(g, bha['paramInt'], 300, 4650, 'PAIntStr'))
    lpa = log(g, 500, 4200, msg_pin=mpa, name='LogPanelAction'); ex(evP, lpa)
    tpa = trace(g, 800, 4200, msg_pin=mpa, name='TracePanelAction')
    if tpa: ex(lpa, tpa)


# ---- OnShipChanged(type): log; option On and type != NO_SHIP -> Tries = 3 -> OnCheck
tstr = bystr(g, evS['shipTypeAtDock'], 200, 900, 'ShipTypeStr')
m1 = concat(g, 400, 900, '%s: ship at main dock changed, type ' % MOD, tstr, ' (2 supply embark, 3 supply trade, 6 none)')
ts = trace(g, 250, 350, msg_pin=m1, name='TraceShip')
l1 = log(g, 500, 600, msg_pin=m1, name='LogShip')
if ts: ex(evS, ts); ex(ts, l1)
else: ex(evS, l1)
sst = g.setv('ShipType', INT, 800, 600, name='SetShipType')
bti = g.call(KML + ':Conv_ByteToInt', 'ShipTypeInt', 650, 800); link(evS['shipTypeAtDock'], bti['InByte']); link(bti['ReturnValue'], sst['ShipType'])
ex(l1, sst)
opt = api_call(g, 'ReadModOptionValue', 1050, 800, name='ReadOpt', optionId=OPT, fallbackValue='On')
isoff = g.call(KSTR + ':EqualEqual_StriStri', 'OptIsOff', 1300, 900, B='Off'); link(opt['ReturnValue'], isoff['A'])
notoff = g.call(KML + ':Not_PreBool', 'OptOn', 1500, 900); link(isoff['ReturnValue'], notoff['A'])
isship = g.call(KSTR + ':EqualEqual_StrStr', 'IsCrateShip', 1300, 1050, B=CRATE_SHIP); link(tstr, isship['A'])
both = g.call(KML + ':BooleanAND', 'OnAndShip', 1700, 950); link(notoff['ReturnValue'], both['A']); link(isship['ReturnValue'], both['B'])
bgo = g.branch(1100, 600, 'BrGo'); link(both['ReturnValue'], bgo['Condition']); ex(sst, opt); ex(opt, bgo)
stt = g.setv('Tries', INT, 1350, 600, value=TRIES, name='SetTries'); ex(bgo, stt)
# v2: a one-shot timer instead of a direct call node (a call to OnCheck pasted before the event exists loses its pins)
spd = g.setv('RetryPending', BOOL, 1600, 600, value='true', name='FirstPending'); ex(stt, spd)
ck = g.call(KSL + ':K2_SetTimer', 'FirstCheck', 1850, 600, FunctionName='OnCheck', Time=FIRST_S, bLooping='false')
link(selfn(g, 1700, 800, 'SelfF')['self'], ck['Object']); ex(spd, ck)

# ---- OnCheck: read docked ship; find reject choice; select it
evC = g.custom_event('OnCheck', [], 0, 2000)
srp0 = g.setv('RetryPending', BOOL, 200, 2000, value='false', name='NotPending'); ex(evC, srp0)
sg = g.get('Sys', SYS_T, 300, 2250, name='SysGet')
bsv = g.branch(450, 2000, 'BrSys'); link(is_valid(g, sg['Sys'], 450, 2250, 'SysValid'), bsv['Condition']); ex(srp0, bsv)
info = g.call(SYS + ':GetCurrentlyDockedShipInfo', 'ShipInfo', 600, 2300); link(sg['Sys'], info['self'])
bk = struct_node(g, 'Break', SHIPDATA, ['shipType', 'shipState', 'asSupplyShip'], 850, 2300, 'BreakShip'); link(info['ReturnValue'], bk['DockableShipData'])
bs = struct_node(g, 'Break', SSTATE, ['choices', 'choiceIdx'], 1100, 2400, 'BreakSupply'); link(bk['asSupplyShip'], bs['SupplyShipState'])
sch = g.setv('Choices', ARR(CHOICE_T), 700, 2000, name='SetChoices'); link(bs['choices'], sch['Choices']); ex(bsv, sch)
sci = g.setv('CurIdx', INT, 950, 2000, name='SetCurIdx'); link(bs['choiceIdx'], sci['CurIdx']); ex(sch, sci)
sri = g.setv('RejectIdx', INT, 1200, 2000, value='-1', name='ResetRejectIdx'); ex(sci, sri)
chg = g.get('Choices', ARR(CHOICE_T), 1300, 2250, name='ChoicesGetLoop')
lp = g.macro('ForEachLoop', CHOICE_T, 1450, 2000, name='ChoiceLoop'); link(chg['Choices'], lp['Array']); ex(sri, lp, 'then', 'Exec')
bc = struct_node(g, 'Break', SCHOICE, ['isRejectChoice'], 1700, 2300, 'BreakChoice'); link(lp['Array Element'], bc['SupplyShipChoice'])
brj = g.branch(1750, 2000, 'BrIsReject'); link(bc['isRejectChoice'], brj['Condition']); ex(lp, brj, 'LoopBody')
srj = g.setv('RejectIdx', INT, 2000, 2000, name='SetRejectIdx'); link(lp['Array Index'], srj['RejectIdx']); ex(brj, srj)
# Completed: log state
nch = g.arr('Array_Length', CHOICE_T, 1800, 1700, name='ChoiceCount', pure=True)
link(g.get('Choices', ARR(CHOICE_T), 1650, 1650, name='ChoicesGetCount')['Choices'], nch['TargetArray'])
rjg = g.get('RejectIdx', INT, 1900, 1550, name='RejectGet')
cig = g.get('CurIdx', INT, 1900, 1450, name='CurGet')
m2 = concat(g, 2200, 1500, '%s:   docked ship type ' % MOD, bystr(g, bk['shipType'], 2000, 1350, 'DockedTypeStr'),
            ', state ', bystr(g, bk['shipState'], 2000, 1300, 'DockedStateStr'),
            ', choices ', istr(g, nch['ReturnValue'], 2000, 1250, 'CountStr'),
            ', selected ', istr(g, cig['CurIdx'], 2000, 1200, 'CurStr'),
            ', reject choice ', istr(g, rjg['RejectIdx'], 2000, 1150, 'RejStr'),
            ', tries left ', istr(g, g.get('Tries', INT, 1900, 1100, name='TriesGetLog')['Tries'], 2000, 1100, 'TriesStr'))
l2 = log(g, 2300, 1800, msg_pin=m2, name='LogState'); ex(lp, l2, 'Completed')
t2 = trace(g, 2300, 1650, msg_pin=m2, name='TraceState')
if t2: ex(l2, t2); after_state = t2
else: after_state = l2
# found?  RejectIdx >= 0
hasr = g.call(KML + ':GreaterEqual_IntInt', 'HasReject', 2600, 2200, B='0'); link(rjg['RejectIdx'], hasr['A'])
bhr = g.branch(2650, 2000, 'BrHasReject'); link(hasr['ReturnValue'], bhr['Condition']); ex(after_state, bhr)
# already selected?
same = g.call(KML + ':EqualEqual_IntInt', 'AlreadyReject', 2850, 2250); link(rjg['RejectIdx'], same['A']); link(cig['CurIdx'], same['B'])
bsm = g.branch(2900, 2000, 'BrAlready'); link(same['ReturnValue'], bsm['Condition']); ex(bhr, bsm)
lsm = log(g, 3200, 1750, msg='%s:   Refuse supplies already selected' % MOD, name='LogAlready'); ex(bsm, lsm)
mk = struct_node(g, 'Make', PA + 'HudAction', ['action', 'paramInt'], 3150, 2300, 'MakeChoose')
mk.set('action', 'chooseSupplyPackage'); link(rjg['RejectIdx'], mk['paramInt'])
pg = g.get('Panel', PANEL_T, 3150, 2550, name='PanelGet')
bpv = g.branch(3150, 2000, 'BrPanel'); link(is_valid(g, pg['Panel'], 3150, 2650, 'PanelValid'), bpv['Condition']); ex(bsm, bpv, 'else')
# v4: back to ReceiveHudAction: the exe shows 0x144baf320 is at vtable+0x558 = ReceiveHudAction_Implementation
# (execHudAction only broadcasts OnHudActionDelegate). v3 note was wrong: HudAction (the panel's outgoing action, which DockedBoatPanel_Supplies intercepts: exe 0x144baf320 sits in the
# vtable slot whose base 0x144baea50 broadcasts OnHudActionDelegate). v2 used ReceiveHudAction: no effect.
rha = g.call(PA + 'ArcoWidgetBase:ReceiveHudAction', 'ChooseReject', 3400, 2000); link(pg['Panel'], rha['self']); link(mk['HudAction'], rha['action'])
# v6 PROBES before the real send: A = unknown action (must be passed up), B = chooseSupplyPackage -1
# (handler matches -> sim refuses -> NOT passed up; handler doesn't match -> passed up)
prev_ = (bpv, 'then')
for tag, act, idx in []:
    lpb = log(g, 3400, 2600 + 300 * ord(tag), msg='%s: DIAG probe %s: %s %s' % (MOD, tag, act, idx), name='LogProbe' + tag)
    tpb = trace(g, 3650, 2600 + 300 * ord(tag), msg='%s: DIAG probe %s: %s %s' % (MOD, tag, act, idx), name='TraceProbe' + tag)
    mkp = struct_node(g, 'Make', PA + 'HudAction', ['action', 'paramInt'], 3900, 2700 + 300 * ord(tag), 'MakeProbe' + tag)
    mkp.set('action', act); mkp.set('paramInt', idx)
    rp = g.call(PA + 'ArcoWidgetBase:ReceiveHudAction', 'Probe' + tag, 4100, 2600 + 300 * ord(tag)); link(pg['Panel'], rp['self']); link(mkp['HudAction'], rp['action'])
    ex(prev_[0], lpb, prev_[1])
    if tpb: ex(lpb, tpb); ex(tpb, rp)
    else: ex(lpb, rp)
    prev_ = (rp, 'then')
ex(prev_[0], rha, prev_[1])
# verify: read the ship again
info2 = g.call(SYS + ':GetCurrentlyDockedShipInfo', 'ShipInfoAfter', 3500, 2400); link(sg['Sys'], info2['self'])
bk2 = struct_node(g, 'Break', SHIPDATA, ['asSupplyShip'], 3750, 2400, 'BreakShipAfter'); link(info2['ReturnValue'], bk2['DockableShipData'])
bs2 = struct_node(g, 'Break', SSTATE, ['choiceIdx'], 3950, 2450, 'BreakSupplyAfter'); link(bk2['asSupplyShip'], bs2['SupplyShipState'])
m3 = concat(g, 4000, 2600, '%s:   sent chooseSupplyPackage ' % MOD, istr(g, rjg['RejectIdx'], 3900, 2700, 'RejStr2'),
            ', selected now ', istr(g, bs2['choiceIdx'], 3900, 2750, 'NowStr'))
l3 = log(g, 3700, 2000, msg_pin=m3, name='LogSent'); ex(rha, l3)
t3 = trace(g, 3950, 1850, msg_pin=m3, name='TraceSent')
if t3: ex(l3, t3)
if DIAG:
    # v5 DIAG: our panel's class, and every DockedBoatPanel_Supplies widget that exists -> log + send the same action
    pcls = g.call(GS + ':GetObjectClass', 'PanelClass', 4300, 1500); link(pg['Panel'], pcls['Object'])
    pcn = g.call(KSL + ':GetClassDisplayName', 'PanelClassName', 4500, 1500); link(pcls['ReturnValue'], pcn['Class'])
    mpc = concat(g, 4700, 1400, '%s: DIAG our panel ' % MOD, gname(g, pg['Panel'], 4500, 1350, 'PanelName'), ' class ', pcn['ReturnValue'])
    lpc = log(g, 4300, 1800, msg_pin=mpc, name='LogPanelClass')
    tpc = trace(g, 4600, 1800, msg_pin=mpc, name='TracePanelClass')
    vt = g.call(KSL + ':K2_SetTimer', 'VerifyTimer', 4300, 2000, FunctionName='OnVerify', Time='1.0', bLooping='false')
    if tpc: ex(lpc, tpc); ex(tpc, vt)
    else: ex(lpc, vt)
    link(selfn(g, 4200, 2200, 'SelfV')['self'], vt['Object'])

    evV = g.custom_event('OnVerify', [], 0, 5200)
    sgv = g.get('Sys', SYS_T, 200, 5450, name='SysGetV')
    iv = g.call(SYS + ':GetCurrentlyDockedShipInfo', 'ShipInfoVerify', 300, 5500); link(sgv['Sys'], iv['self'])
    bkv = struct_node(g, 'Break', SHIPDATA, ['shipType', 'asSupplyShip'], 550, 5500, 'BreakShipV'); link(iv['ReturnValue'], bkv['DockableShipData'])
    bsv2 = struct_node(g, 'Break', SSTATE, ['choiceIdx'], 800, 5550, 'BreakSupplyV'); link(bkv['asSupplyShip'], bsv2['SupplyShipState'])
    mv = concat(g, 900, 5700, '%s:   1 s later: docked type ' % MOD, bystr(g, bkv['shipType'], 800, 5800, 'VTypeStr'), ', selected ', istr(g, bsv2['choiceIdx'], 800, 5850, 'VSelStr'))
    lv = log(g, 400, 5200, msg_pin=mv, name='LogVerify'); ex(evV, lv)
    tv = trace(g, 700, 5200, msg_pin=mv, name='TraceVerify')
    if tv: ex(lv, tv)
    ex(t3 if t3 else l3, lpc)

lnp = log(g, 3400, 2250, msg='%s:   invisible supply panel missing' % MOD, name='LogNoPanel'); ex(bpv, lnp, 'else')
# no reject choice yet -> retry (choices may not be filled at the docking moment)
tg = g.get('Tries', INT, 2700, 2600, name='TriesGet')
tdec = g.call(KML + ':Subtract_IntInt', 'TriesMinus', 2850, 2650, B='1'); link(tg['Tries'], tdec['A'])
more = g.call(KML + ':Greater_IntInt', 'TriesLeft', 3050, 2650, B='0'); link(g.get('Tries', INT, 2900, 2550, name='TriesGetAfter')['Tries'], more['A'])  # read after DecTries (v8 fix)
pnd = g.get('RetryPending', BOOL, 3050, 2800, name='PendingGet')
npnd = g.call(KML + ':Not_PreBool', 'NotPendingNow', 3200, 2800); link(pnd['RetryPending'], npnd['A'])
okr = g.call(KML + ':BooleanAND', 'RetryOk', 3350, 2700); link(more['ReturnValue'], okr['A']); link(npnd['ReturnValue'], okr['B'])
std = g.setv('Tries', INT, 2900, 2950, name='DecTries'); link(tdec['ReturnValue'], std['Tries']); ex(bhr, std, 'else')
brt = g.branch(3150, 2950, 'BrRetry'); link(okr['ReturnValue'], brt['Condition']); ex(std, brt)
srp = g.setv('RetryPending', BOOL, 3400, 2950, value='true', name='Pending'); ex(brt, srp)
tmr = g.call(KSL + ':K2_SetTimer', 'RetryTimer', 3650, 2950, FunctionName='OnCheck', Time=RETRY_S, bLooping='false')
link(selfn(g, 3500, 3150, 'SelfT')['self'], tmr['Object']); ex(srp, tmr)
lrt = log(g, 3950, 2950, msg='%s:   no Refuse supplies choice yet, checking again in %s s' % (MOD, RETRY_S), name='LogRetry'); ex(tmr, lrt)
lgu = log(g, 3400, 3250, msg='%s:   no Refuse supplies choice, giving up' % MOD, name='LogGiveUp'); ex(brt, lgu, 'else')
# Sys missing
lns = log(g, 700, 1750, msg='%s:   ArcoSystems not valid' % MOD, name='LogNoSys'); ex(bsv, lns, 'else')

txt = g.text()
open(OUT + '/BP_MapLoad.txt', 'w', encoding='utf-8').write(txt)

# ---- validation: every exec input linked; every link target exists; LogMessage gated (except Trace*)
names = {n.name for n in g.nodes}
bad = []
for n in g.nodes:
    for p in n.pins:
        for q in p.links:
            if q.node.name not in names: bad.append('dangling %s.%s' % (n.name, p.name))
        if p.t['cat'] == 'exec' and not p.out and not p.links and not n.cls.endswith('K2Node_Knot'):
            bad.append('unlinked exec in %s.%s' % (n.name, p.name))
    if 'LogMessage' in ' '.join(n.header) and not n.name.startswith('Trace'):
        ins = n['execute'].links
        if not ins or not all(q.node.cls.endswith('IfThenElse') for q in ins): bad.append('ungated log ' + n.name)
print('BP_MapLoad nodes', len(g.nodes), 'problems', bad or 'none')
