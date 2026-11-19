// Added to the native editor agent so logic operations run on its UI thread.
function logicTree(runningOnly) {
  const sim = simulationInfo();
  if (!sim.remote_initialized || (sim.state !== 1 && (runningOnly || sim.state !== 2))) {
    throw new Error(runningOnly ? "Start or resume interactive simulation first" : "Start interactive simulation first");
  }
  return treeInstance();
}

function logicSymbol(ref, model) {
  const found = component(ref);
  if (parameterInfo({ref: found.id}).info[1] !== model) {
    throw new Error(ref + " is not a " + model);
  }
  return ptr(found.id);
}

function logicSwitch(ref, tree) {
  const symbol = logicSymbol(ref, "INTERACTIVE_DIGITAL_CONSTANT");
  const mouse = method("Od101dll.dll", "?GetMouseInteractInterface@CIITSymbolComp@@",
    "pointer", ["pointer"])(symbol);
  if (mouse.isNull()) throw new Error("Switch has no runtime interaction interface: " + ref);

  const type = rtti(mouse);
  if (type.name !== ".?AVCInteractiveComponentInstrument@@") {
    throw new Error("Unexpected switch interaction interface: " + ref);
  }
  const instrument = mouse.sub(type.offset);
  const getState = method("interact.dll", "?GetAnimationState@CInteractiveComponentInstrument@@",
    "int", ["pointer", "pointer"]);
  const previous = getState(instrument, tree);
  if (previous !== 0 && previous !== 1) throw new Error("Switch state is not binary: " + ref);
  return {ref, tree, mouse, instrument, getState, previous};
}

function applyLogicInput({ref, tree, mouse, instrument, getState, previous}, state) {
  if (previous !== state) {
    // The stock switch handles event 2 on subcomponent 1. The handler takes
    // the IMouseInteract subobject, while GetAnimationState takes the full object.
    method("interact.dll", "?OnSubCompIMouseEvent@CInteractiveComponentInstrument@@",
      "int", ["pointer", "pointer", "uint", "int", "int"])(mouse, tree, 2, 0, 1);
    if (getState(instrument, tree) !== state) throw new Error("Switch did not reach requested state: " + ref);
  }
  return {ref, previous, state, changed: previous !== state};
}

editorHandlers.setLogicInput = ({ref, state}) => {
  return applyLogicInput(logicSwitch(ref, logicTree(true)), state);
};

function readLogicIndicators(refs, tree) {
  const read = method("Od101dll.dll", "?GetSubComponentPrp@CIITSymbolComp@@",
    "uint", ["pointer", "int", "int", "pointer"]);
  const indicators = refs.map(ref => {
    const symbol = logicSymbol(ref, "PROBE_DIG_RED");
    // The stock indicator's rays are positions 7–14 in its definition
    // and slots 6–13 in the runtime graphics accessor.
    const rays = Array.from({length: 8}, (_, index) => read(symbol, 0, 6 + index, tree));
    const state = rays.every(value => value === 0) ? 0 : rays.every(value => value === 1) ? 1 : null;
    return {ref, state, lit: state === null ? null : state === 1};
  });
  return {indicators, source: "native runtime graphic properties"};
}

editorHandlers.readLogicIndicators = ({refs}) => {
  return readLogicIndicators(refs, logicTree(false));
};

editorHandlers.f5LogicSnapshot = ({input_refs, changes, indicator_refs}) => {
  const tree = logicTree(true);
  const switches = input_refs.map(ref => logicSwitch(ref, tree));
  const indicators = indicator_refs.length ? readLogicIndicators(indicator_refs, tree).indicators : [];
  // Sample a row in a separate call, after its settle time. Keeping the writes
  // in one UI-thread dispatch avoids idle frames between inputs in a row.
  const inputs = switches.map(switchState => {
    const ref = switchState.ref;
    if (Object.prototype.hasOwnProperty.call(changes, ref)) {
      return [ref, applyLogicInput(switchState, changes[ref]).state];
    }
    return [ref, switchState.previous];
  });
  const clock = interactiveStatus();
  return {inputs: Object.fromEntries(inputs), indicators, simulation_time_s: clock.simulation_time_s};
};
