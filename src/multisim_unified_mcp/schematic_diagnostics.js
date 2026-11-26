// Runs on the pinned Native editor's UI thread alongside its existing handlers.
editorHandlers.circuitStateWithPower = args => {
  const state = circuitState(args);
  const countPorts = method('CIRCEXP.dll', '?GetPortCount@CiComponent@@', 'int', ['pointer']);
  const getPort = method('CIRCEXP.dll', '?GetPort@CiComponent@@QBEPAVCiPort@@H@Z', 'pointer', ['pointer', 'int']);
  const getAttribute = method('CIRCEXP.dll', '?GetAttribute@CiElement@@', 'pointer', ['pointer', 'uint']);
  const getNode = method('CIRCEXP.dll', '?GetNode@CiComponent@@', 'pointer', ['pointer', 'int']);

  for (const part of state.components) {
    const component = ciObject(ptr(part.id));
    const count = countPorts(component);
    const visible = new Set(part.pins.map(pin => pin.name));
    const pins = [];
    for (let i = 0; i < count; i++) {
      const port = getPort(component, i);
      if (port.isNull()) continue;
      const name = returnedString('CIRCEXP.dll', '?GetLocalName@CiElement@@', port);
      if (visible.has(name)) continue;
      const attributes = getAttribute(port, 5);
      if (attributes.isNull() || !/CiaCollString/.test(rtti(attributes).name)) continue;
      // In the pinned build these two fields back the physical pin table.
      const type = instanceString('CIRCEXP.dll', '?GetParam@CiaCollString@@', attributes, [8], ['int']).toLowerCase();
      if (type !== 'power' && type !== 'ground') continue;
      const number = instanceString('CIRCEXP.dll', '?GetParam@CiaCollString@@', attributes, [6], ['int']);
      const node = getNode(component, i);
      const net = node.isNull() ? null : returnedString('CIRCEXP.dll', '?GetLocalName@CiElement@@', node) || null;
      pins.push({name, number: number || null, type, net});
    }
    if (pins.length) part.hidden_power_pins = pins;
  }
  return state;
};

editorHandlers.layoutWithPowerSymbols = () => {
  const {components: parts} = componentBounds();
  const powerSymbols = new Set(['VCC', 'VDD', 'VEE', 'VSS', 'GND', 'GROUND', 'DIGITAL_GROUND']);
  for (const part of parts) {
    if (part.pins.length !== 1) continue;
    const name = parameterInfo({ref: part.id}, part).info[1];
    if (powerSymbols.has(name)) part.power_symbol = true;
  }
  return {components: parts, wires: wires().wires, page: pageGeometry().bounds};
};

editorHandlers.branchState = () => {
  const state = circuitState();
  for (const wire of state.wires) wire.user_named = persistentWireLabel(wire.id) !== null;
  return state;
};

function uprightReflections(ref) {
  const part = component(ref);
  const determinant = ([a,b,c,d]) => a*d-b*c;
  const partDeterminant = determinant(part.transform);
  const changed = [];
  for (const label of componentLabels({ref: part.id}, part).labels) {
    if (!label.text || partDeterminant * determinant(label.transform) >= 0) continue;
    const graphic = method('Od101dll.dll','?GetGraphic@CODComponent@@QAEP','pointer',['pointer'])(ptr(label.id));
    method('Od101dll.dll','?Scale@CODGraphic@@','void',['pointer','float','float','float','float'])(graphic,1,-1,0,0);
    moveComponentLabel({ref: part.id, kind: label.kind, ...label.center});
    changed.push({ref: part.ref, kind: label.kind});
  }
  return changed;
}

editorHandlers.uprightLabels = ({refs}) => ({changed: refs.flatMap(uprightReflections)});

editorHandlers.moveForPlacement = args => movePreservingGeometry(args);
