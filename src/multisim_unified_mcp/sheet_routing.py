"""Find groups of competing routes and give them common lanes to search."""

from collections import defaultdict
from itertools import combinations

from .wiring import contacts, normalized, pin_exit, proper_crossing, score, wire_networks


def routing_neighborhoods(state, blocks, limit=3):
    wires = state['wires']
    paths = {w['id']: normalized((p['x'], p['y']) for p in w['points']) for w in wires}
    owners = {ref: block for block, refs in blocks.items() for ref in refs}
    groups = wire_networks(wires)
    neighbors = defaultdict(dict)
    burden = defaultdict(int)
    by_block = defaultdict(set)
    positions = {wire['id']: index for index, wire in enumerate(wires)}
    for wire in wires:
        for end in (wire['source'], wire['target']):
            if end.get('ref') in owners:
                by_block[owners[end['ref']]].add(wire['id'])
    for first, second in combinations(wires, 2):
        a, b = first['id'], second['id']
        if groups[first['source']['id']] == groups[second['source']['id']]:
            continue
        hits = contacts(paths[a], first, [(second, paths[b])])
        weight = sum(1 if (x, y) == (u, v) and proper_crossing(paths[a], paths[b], x, y) else 6
                     for _, x, y, u, v in hits)
        if weight:
            neighbors[a][b] = neighbors[b][a] = weight
            burden[a] += weight
            burden[b] += weight

    def order(ident):
        return -burden[ident], -score(paths[ident])[0], positions[ident]

    # Keep several independent regions in the budget. A crowded power rail or
    # feedback route can otherwise monopolize the candidate windows.
    seeds = sorted(by_block.values(), key=lambda group: (-sum(burden[i] for i in group),
                                                        min(positions[i] for i in group)))
    used = set()
    result = []
    for group in seeds:
        core = sorted(group, key=order)[:5]
        adjacent = {peer for ident in core for peer in neighbors[ident]} - set(core)
        nearby = sorted(adjacent, key=lambda i: (-sum(neighbors[i].get(c, 0) for c in core), order(i)))
        window = frozenset([*core, *nearby[:8 - len(core)]])
        if len(window) < 2 or any(len(window & old) >= min(len(window), len(old)) - 1 for old in used):
            continue
        used.add(window)
        result.append(window)
        if len(result) == limit:
            break
    return result


def shared_channel_options(state, wire_ids):
    """Generate common tracks and perimeter detours for a neighborhood of wires."""
    wires = [wire for wire in state['wires'] if wire['id'] in wire_ids]
    bodies = {part['id']: part['body_bounds'] for part in state['components']}
    ends = [w[key] for w in wires for key in ('source', 'target')]
    page = state['page']['bounds']
    bounds = [(min(p[key] for p in ends), max(p[key] for p in ends)) for key in ('x', 'y')]
    axes = [('left', 'right'), ('top', 'bottom')]
    lanes = []
    for (near, far), (edge1, edge2) in zip(bounds, axes):
        slots = {round((near + (far - near) * ratio / 8) / 9) * 9 for ratio in range(1, 8)}
        # Route outside the signals in this window, without reaching for the
        # page edge when there is a nearer open corridor.
        slots.update((near - distance) // 9 * 9 for distance in (18, 36, 54))
        slots.update((far + distance + 8) // 9 * 9 for distance in (18, 36, 54))
        lanes.append(sorted(slot for slot in slots if page[edge1] < slot < page[edge2]))
    result = {}
    for wire in wires:
        terminals = [wire[key] for key in ('source', 'target')]
        start, end = [(terminal['x'], terminal['y']) for terminal in terminals]
        leads = []
        for location, terminal in zip((start, end), terminals):
            body = bodies.get(terminal.get('component_id'))
            dx, dy = (pin_exit(terminal, body) if body else None) or (0, 0)
            leads.append((location[0] + 18 * dx, location[1] + 18 * dy))
        first, last = leads
        options = []
        for axis, tracks in enumerate(lanes):
            for track in tracks:
                left, right = [tuple(track if i == axis else v for i, v in enumerate(p)) for p in leads]
                options.append(normalized([start, first, left, right, last, end]))
                # A second leg around the window gives feedback a route when
                # the one-turn shortcut would run through the component bank.
                if bounds[axis][0] <= track <= bounds[axis][1]:
                    continue
                a, b = (1 - axis), axis
                for baseline in (min(first[a], last[a]) - 18, max(first[a], last[a]) + 18):
                    via = [(p[b], baseline) if axis == 0 else (baseline, p[b]) for p in leads]
                    corner = (track, baseline) if axis == 0 else (baseline, track)
                    options.extend((normalized([start, first, left, corner, via[1], last, end]),
                                    normalized([start, first, via[0], corner, right, last, end])))
        result[wire['id']] = options
    return result


def compact_corridors(state, movable_refs):
    """Close unused space with a monotone translation on one side of a clear strip."""
    parts = state['components']
    wires = state['wires']
    notes = [note['bounds'] for note in state.get('texts', ()) if note.get('bounds')]
    paths = [[(p['x'], p['y']) for p in wire['points']] for wire in wires]
    junctions = {point['id']: (point['x'], point['y']) for wire in wires for point in (wire['source'], wire['target'])
                 if not point.get('ref') and not point.get('component_id')}
    result = []
    for axis, (low, high) in enumerate((('left', 'right'), ('top', 'bottom'))):
        boxes = {part['ref']: part.get('bounds', part['body_bounds']) for part in parts}
        intervals = [(box[low], box[high]) for box in [*boxes.values(), *notes]]
        intervals.extend((point[axis], point[axis]) for path in paths for point in path)
        if not intervals:
            continue
        intervals.sort()
        edge = intervals[0][1]
        for start, end in intervals[1:]:
            shift = int((start - edge - 54) // 9) * 9
            if shift >= 18:
                crossing = sum((a[axis] <= edge and b[axis] >= start) or
                               (b[axis] <= edge and a[axis] >= start)
                               for path in paths for a, b in zip(path, path[1:]))
                for side in (-1, 1):
                    def selected(value):
                        return value <= edge if side < 0 else value >= start
                    moved = {ref for ref, box in boxes.items()
                             if selected(box[high] if side < 0 else box[low])}
                    if (crossing and moved and moved <= movable_refs
                            and not any(selected(box[high] if side < 0 else box[low]) for box in notes)):
                        delta = tuple(-side * shift if index == axis else 0 for index in (0, 1))
                        shift_routes = []
                        for wire, path in zip(wires, paths):
                            points = [(x + delta[0], y + delta[1]) if selected((x, y)[axis]) else (x, y)
                                      for x, y in path]
                            if points != path:
                                shift_routes.append({'wire_id': wire['id'], 'points': [
                                    dict(x=x, y=y) for x, y in normalized(points)]})
                        result.append((crossing * shift, {
                            'moves': {ref: delta for ref in sorted(moved)}, 'routes': shift_routes,
                            'junction_moves': {ident: delta for ident, point in junctions.items() if selected(point[axis])},
                        }))
            edge = max(edge, end)
    return [option for _, option in sorted(result, key=lambda item: -item[0])[:4]]
