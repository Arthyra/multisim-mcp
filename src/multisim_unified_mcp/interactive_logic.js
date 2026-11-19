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

editorHandlers.setLogicInput = ({ref, state}) => {
  const tree = logicTree(true);
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
  if (previous !== state) {
    // The stock switch handles event 2 on subcomponent 1. The handler takes
    // the IMouseInteract subobject, while GetAnimationState takes the full object.
    method("interact.dll", "?OnSubCompIMouseEvent@CInteractiveComponentInstrument@@",
      "int", ["pointer", "pointer", "uint", "int", "int"])(mouse, tree, 2, 0, 1);
    if (getState(instrument, tree) !== state) throw new Error("Switch did not reach requested state: " + ref);
  }
  return {ref, previous, state, changed: previous !== state};
};

editorHandlers.readLogicIndicators = ({refs}) => {
  const tree = logicTree(false);
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
};
