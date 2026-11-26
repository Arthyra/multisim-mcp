"""Plan block translations and reroute their connections on a complete sheet."""

from collections import defaultdict
from itertools import combinations
from statistics import median

from .layout_workflow import checked_layout_issues
from .sheet_routing import compact_corridors, routing_neighborhoods, shared_channel_options
from .wiring import PLACEMENT_METRICS, compare_placements, pin_exit, plan_local_routing, preview_route_state, terminal_groups


def infer_layout_blocks(state):
    """Group repeated, aligned parts that share nearby native electrical peers."""
    parts = state['components']
    reference_by_id = {part['id']: part['ref'] for part in parts}
    peers = defaultdict(set)
    for terminals in terminal_groups(state['wires']):
        refs = {reference_by_id.get(component, component) for component, _ in terminals}
        # A power/ground bus that reaches most of the sheet is not a useful
        # indication that two parts belong to the same functional region.
        if 2 <= len(refs) <= 4:
            for ref in refs:
                peers[ref].update(refs - {ref})
    fixed = {part['ref'] for part in parts if part['ref'].upper() in {'0', 'GND', 'VCC', 'VDD', 'VSS'}
             or any(pin['name'].upper() in {'VCC', 'GND', 'VDD', 'VSS'} for pin in part['pins'])
             and len(part['pins']) == 1}
    signatures = {}
    for part in parts:
        if part['ref'] in fixed:
            continue
        signatures[part['ref']] = sorted((pin['name'], pin_exit(pin, part['body_bounds']) or (0, 0))
                                         for pin in part['pins'])
    origins = {part['ref']: (part['body_bounds']['left'], part['body_bounds']['top'])
               for part in parts if part['ref'] in signatures}
    repeated_links = {}
    for ref, (x, y) in origins.items():
        repeated_links[ref] = {
            (tuple(signatures[peer]), round((origins[peer][0] - x) / 18),
             round((origins[peer][1] - y) / 18))
            for peer in peers[ref] if peer in origins
        }
    used = set(fixed)
    blocks = {}
    for part in parts:
        ref = part['ref']
        if ref in used:
            continue
        box = part['body_bounds']
        similar = []
        for other in parts:
            name = other['ref']
            if name in used or name == ref or signatures[ref] != signatures[name]:
                continue
            b = other['body_bounds']
            dx, dy = abs(b['left'] - box['left']), abs(b['top'] - box['top'])
            along_row_or_column = min(dx, dy) <= 18
            shared_peer = bool(peers[ref] & peers[name])
            direct_peer = name in peers[ref] and max(dx, dy) <= 216
            same_link_shape = bool(repeated_links[ref] & repeated_links[name])
            if along_row_or_column and (shared_peer or direct_peer or same_link_shape):
                similar.append(name)
        block = [ref, *similar]
        blocks[ref] = block
        used.update(block)
    return blocks, sorted(fixed)


def block_proposals(state, blocks, fixed_refs):
    parts = defaultdict(list)
    for part in state['components']:
        parts[part['ref']].append(part)
    members = [ref for refs in blocks.values() for ref in refs]
    if not members or any(not refs for refs in blocks.values()) or len(set(members)) != len(members):
        raise ValueError('Provide nonempty blocks with each component in at most one block')
    if any(len(parts[ref]) != 1 for ref in set(members) | set(fixed_refs)):
        raise ValueError('Use unambiguous component references from the active sheet')
    owner = {ref: block for block, refs in blocks.items() for ref in refs}
    unlocked = set(members) - set(fixed_refs)
    route_scope = {wire['id'] for wire in state['wires'] if any(
        wire[key].get('ref') in unlocked for key in ('source', 'target'))}
    anchors = defaultdict(list)
    pin_anchors = defaultdict(list)
    junction_owners = defaultdict(set)
    for wire in state['wires']:
        a, b = wire['source'], wire['target']
        for end, peer in ((a, b), (b, a)):
            ref, next_ref = end.get('ref'), peer.get('ref')
            if ref in unlocked and next_ref in parts and owner[ref] != owner.get(next_ref):
                offset = peer['y'] - end['y']
                anchors[owner[ref]].append(offset)
                pin_anchors[ref].append(offset)
            if not ref and not end.get('component_id'):
                junction_owners[end['id']].add(next_ref)

    def grid(value):
        return round(value / 9) * 9

    seen = set()
    proposals = []

    def add(name, offsets, reroute=(), individual=None):
        moves = {ref: delta for block, delta in offsets.items() if any(delta)
                 for ref in blocks[block] if ref in unlocked}
        moves.update(individual or {})
        moves.update({ident: moves[next(iter(peers))] for ident, peers in junction_owners.items()
                      if peers <= moves.keys() and len({moves[ref] for ref in peers}) == 1})
        key = (tuple(sorted(moves.items())), frozenset(reroute))
        if key not in seen:
            seen.add(key)
            proposals.append({'name': name, 'moves': moves, 'reroute': reroute, 'sheet_routing': True})

    add('reroute_in_place', {}, route_scope)
    movable = [block for block, refs in blocks.items() if set(refs) & unlocked]
    centers = {block: median((parts[ref][0]['body_bounds']['left'] + parts[ref][0]['body_bounds']['right']) / 2
                             for ref in blocks[block]) for block in movable}
    center = median(centers.values()) if centers else 0
    aligned = {block: (0, grid(max(-36, min(36, median(anchors[block])))))
               for block in movable if anchors[block]}
    for block in movable:
        dx = -27 if centers[block] < center else 27
        dy = aligned.get(block, (0, 0))[1]
        add(f'{block}:open_corridor', {block: (dx, 0)})
        add(f'{block}:shorten_connections', {block: (-dx, 0)})
        add(f'{block}:small_shift', {block: (-dx // 3, 0)})
        add(f'{block}:small_clearance', {block: (dx // 3, 0)})
        if dy:
            add(f'{block}:align_ports', {block: (0, dy)})
            add(f'{block}:align_and_shorten', {block: (-dx, dy)})
        # A bank of separate, facing terminals can close the gaps between its rows
        # without reordering them. Preserve the placement of each part's labels.
        if len(blocks[block]) > 1 and all(
            ref in unlocked and len(parts[ref][0]['pins']) == 1 and len(pin_anchors[ref]) == 1
            for ref in blocks[block]
        ):
            bank = sorted((parts[ref][0] for ref in blocks[block]), key=lambda p: p['pins'][0]['y'])
            faces = {pin_exit(part['pins'][0], part['body_bounds']) for part in bank}
            if faces in ({(0, 1)}, {(0, -1)}):
                face_y = next(iter(faces))[1]
                for lead in (18, 27):
                    offsets = {part['ref']: (0, grid(pin_anchors[part['ref']][0] - lead * face_y)) for part in bank}
                    add(f'{block}:align_indicators:{lead}', {}, individual=offsets)
                    if len(bank) <= 4:
                        # Keep the body of the lower indicator away from the
                        # upper signal's horizontal run into its own indicator.
                        order = bank if face_y > 0 else bank[::-1]
                        footprints = [part.get('bounds', part['body_bounds']) for part in order]
                        spacing = max(45, max(a['right'] - b['left'] + 9
                                              for a, b in zip(footprints[::2], footprints[1::2])))
                        stagger = ((spacing + 8) // 9) * 9
                        for shift in (stagger, stagger + 9):
                            staggered = {part['ref']: (shift if i % 2 else 0, offsets[part['ref']][1])
                                         for i, part in enumerate(order)}
                            add(f'{block}:stagger_indicators:{lead}:{shift}', {}, individual=staggered)
            elif faces in ({(1, 0)}, {(-1, 0)}):
                # Anchor on the selected pins but keep clearance for symbol and label boxes.
                slots = []
                for part in bank:
                    y = part['pins'][0]['y']
                    boxes = [part['body_bounds'], *(label['bounds'] for label in part.get('labels', ())
                                                    if label.get('text') and label.get('bounds'))]
                    slots.append((min(box['top'] for box in boxes) - y, max(box['bottom'] for box in boxes) - y))
                for clearance in (9, 18):
                    rows = [part['pins'][0]['y'] + pin_anchors[part['ref']][0] for part in bank]
                    packed = [rows[0]]
                    for i, row in enumerate(rows[1:], 1):
                        gap = slots[i - 1][1] - slots[i][0] + clearance
                        packed.append(max(row, packed[-1] + ((gap + 8) // 9) * 9))
                    correction = grid(median(row - new for row, new in zip(rows, packed)))
                    offsets = {part['ref']: (0, grid(row + correction - part['pins'][0]['y']))
                               for part, row in zip(bank, packed)}
                    add(f'{block}:space_terminal_rows:{clearance}', {}, individual=offsets)
    add('align_blocks', aligned)
    if len(movable) > 1:
        footprints = {
            block: (min(parts[ref][0].get('bounds', parts[ref][0]['body_bounds'])['left'] for ref in blocks[block]),
                    max(parts[ref][0].get('bounds', parts[ref][0]['body_bounds'])['right'] for ref in blocks[block]))
            for block in movable
        }
        for spacing in (-18, 18, 36):
            add(f'respace_blocks:{spacing}', {
                block: (spacing * ((centers[block] > center) - (centers[block] < center)),
                        aligned.get(block, (0, 0))[1]) for block in movable})
        # Try larger moves on the busiest direct inter-block corridors. Separate
        # horizontal and vertical trials keep the search from spreading every block.
        links = defaultdict(int)
        for wire in state['wires']:
            a, b = (owner.get(wire[key].get('ref')) for key in ('source', 'target'))
            if a in centers and b in centers and a != b and centers[a] != centers[b]:
                left, right = sorted((a, b), key=lambda block: centers[block])
                links[left, right] += 1
        for (left, right), count in sorted(links.items(), key=lambda link: -link[1])[:2]:
            if count < 2:
                continue
            width = max(36, min(72, (count + 2) * 9))
            add(f'{left}:{right}:reserve_left', {left: (-width, 0)})
            add(f'{left}:{right}:reserve_right', {right: (width, 0)})
            add(f'{left}:{right}:split_corridor', {left: (-grid(width / 2), 0), right: (grid(width / 2), 0)})
            gap = footprints[right][0] - footprints[left][1]
            # Visit materially different spacings in one preview. Repeatedly
            # taking the same small step can leave a long parallel bus unchanged.
            for proportion in (0.5, 1.0):
                slack = (gap - max(54, (count + 2) * 9)) * proportion
                closing = max(0, int(slack // 9) * 9)
                if closing >= 36:
                    add(f'{left}:{right}:bring_input:{closing}', {left: (closing, 0)})
                    add(f'{left}:{right}:bring_output:{closing}', {right: (-closing, 0)})
            for block in (left, right):
                if anchors[block]:
                    offset = grid(max(-90, min(90, median(anchors[block]))))
                    add(f'{block}:align_block_centers', {block: (0, offset)})
    return proposals


def compose_sheet_edits(seed, edit):
    result = {}
    for field in ('moves', 'junction_moves'):
        before, after = seed.get(field, {}), edit.get(field, {})
        result[field] = {key: tuple(a + b for a, b in zip(before.get(key, (0, 0)), after.get(key, (0, 0))))
                         for key in dict.fromkeys((*before, *after))}
    result['routes'] = list({route['wire_id']: route
                             for route in [*seed['routes'], *edit['routes']]}.values())
    return result


def spacing_trials(state, blocks, fixed_refs):
    """Shortlist translations by how much they shorten actual terminal spans."""
    wires = state['wires']

    def savings(proposal):
        offsets = proposal['moves']
        saved = 0
        for wire in wires:
            a, b = wire['source'], wire['target']
            dx, dy = offsets.get(a.get('ref') or a['id'], (0, 0))
            ex, ey = offsets.get(b.get('ref') or b['id'], (0, 0))
            x, y = a['x'] - b['x'], a['y'] - b['y']
            saved += abs(x) + abs(y) - abs(x + dx - ex) - abs(y + dy - ey)
        return saved

    trials = [(savings(item), item) for item in block_proposals(state, blocks, fixed_refs)
              if item['moves'] and all(dy == 0 and abs(dx) <= 54 for dx, dy in item['moves'].values())]
    by_parts = {}
    for saved, item in sorted(trials, key=lambda trial: -trial[0]):
        if saved > 0:
            by_parts.setdefault(frozenset(item['moves']), item)
    return list(by_parts.values())[:8]


def plan_sheet_layout(state, blocks, fixed_refs, canvas, intersects, orthogonal_path, check_layout):
    proposals = block_proposals(state, blocks, fixed_refs)
    baseline_issues = checked_layout_issues(state, check_layout, intersects)
    expanded = state
    sheet_edit = None
    if canvas:
        width, height = canvas
        page = state['page']
        if width < page['width'] or height < page['height']:
            raise ValueError('Canvas dimensions must be at least the current sheet size')
        if (width, height) != (page['width'], page['height']):
            bounds = {**page['bounds'], 'right': page['bounds']['left'] + width,
                      'bottom': page['bounds']['top'] + height}
            expanded = {**state, 'page': {**page, 'width': width, 'height': height, 'bounds': bounds}}
            sheet_edit = {'operation': 'sheetSize', 'arguments': {'width': width, 'height': height}}

    compared = compare_placements(expanded, proposals, intersects, orthogonal_path)
    baseline = compared['baseline']
    candidates = compared['candidates']
    original_paths = {wire['id']: wire['points'] for wire in state['wires']}
    # Do not trade a new electrical-looking overlap or label collision for a shorter route.
    def inspect_candidate(candidate):
        if 'routes' not in candidate:
            return
        candidate['routes'] = [route for route in candidate['routes']
                               if route['points'] != original_paths[route['wire_id']]]
        drawing = preview_route_state(expanded, candidate['moves'], candidate.get('junction_moves', {}),
                                      candidate['routes'])
        introduced = checked_layout_issues(drawing, check_layout, intersects) - baseline_issues
        if introduced:
            candidate['reason'] = 'New layout issues: ' + ', '.join(sorted({issue[0] for issue in introduced}))

    for candidate in candidates:
        inspect_candidate(candidate)
    def rank(metrics):
        return tuple(metrics[key] for key in PLACEMENT_METRICS)
    def improves(candidate):
        if 'metrics' not in candidate or 'reason' in candidate:
            return False
        metrics = candidate['metrics']
        return (all(metrics[key] <= baseline[key] for key in (*PLACEMENT_METRICS[:4], 'point_contacts', 'label_contacts'))
                and (metrics['bends'] <= baseline['bends'] or (
                    metrics['wrong_pin_approaches'] < baseline['wrong_pin_approaches']
                    and metrics['bends'] <= baseline['bends'] + 1) or (
                    baseline['point_contacts'] - metrics['point_contacts'] > metrics['bends'] - baseline['bends']
                    and metrics['bends'] <= baseline['bends'] + 2))
                and rank(metrics) < rank(baseline))
    improved = [c for c in candidates if improves(c)]
    # Reroute the combined placement again. Appending two independent lists of route
    # edits could cause both blocks to use the same clear corridor.
    distinct_blocks = []
    used_objects = set()
    for candidate in sorted(improved, key=lambda c: rank(c['metrics'])):
        moves = {**candidate['moves'], **candidate.get('junction_moves', {})}
        if moves and used_objects.isdisjoint(moves):
            distinct_blocks.append((candidate['name'], moves))
            used_objects.update(moves)
        if len(distinct_blocks) == 3:
            break
    combined = [{'name': ' + '.join(name for name, _ in group),
                 'moves': {ref: offset for _, moves in group for ref, offset in moves.items()},
                 'sheet_routing': True}
                for size in range(2, len(distinct_blocks) + 1) for group in combinations(distinct_blocks, size)]
    if combined:
        for candidate in compare_placements(expanded, combined, intersects, orthogonal_path)['candidates']:
            inspect_candidate(candidate)
            candidates.append(candidate)
            if improves(candidate):
                improved.append(candidate)

    # Always try the original layout, then spend the remaining budget on the
    # strongest placements. Competing wires get new spatial options together.
    seeds = [{'name': 'current', 'moves': {}, 'routes': [], 'metrics': baseline}]
    seeds += sorted(improved, key=lambda candidate: rank(candidate['metrics']))[:2]
    for seed in seeds:
        name = seed['name'] + ' + shared_channels'
        moves, junctions = seed['moves'], seed.get('junction_moves', {})
        routes = {r['wire_id']: r for r in seed['routes']}
        projected = preview_route_state(expanded, moves, junctions, routes.values())
        for window in routing_neighborhoods(projected, blocks):
            choices = plan_local_routing(projected, intersects, window,
                                         extra_routes=shared_channel_options(projected, window),
                                         preserve_pin_clearance=True)['alternatives']
            for choice in choices:
                proposed_routes = {**routes, **{r['wire_id']: r for r in choice['changes']}}
                proposed = preview_route_state(expanded, moves, junctions, proposed_routes.values())
                if checked_layout_issues(proposed, check_layout, intersects) - baseline_issues:
                    continue
                metrics = compare_placements(proposed, [], intersects, orthogonal_path)['baseline']
                if rank(metrics) >= rank(seed['metrics']):
                    continue
                candidate = {**seed, 'name': name,
                             'metrics': metrics, 'routes': list(proposed_routes.values())}
                inspect_candidate(candidate)
                if not improves(candidate):
                    continue
                seed, projected, routes = candidate, proposed, proposed_routes
                break
        if seed['name'].endswith(' + shared_channels'):
            candidates.append(seed)
            improved.append(seed)

    # Compress a genuinely empty corridor once, instead of incrementally
    # moving neighboring blocks and making the router rebuild the same bus.
    seed = min(improved, key=lambda c: rank(c['metrics']), default=seeds[0])
    moves, junctions = seed['moves'], seed.get('junction_moves', {})
    projected = preview_route_state(expanded, moves, junctions, seed['routes'])
    movable_refs = {ref for refs in blocks.values() for ref in refs} - set(fixed_refs)
    for index, compact in enumerate(compact_corridors(projected, movable_refs), 1):
        planned = compose_sheet_edits(seed, compact)
        drawing = preview_route_state(expanded, planned['moves'], planned['junction_moves'], planned['routes'])
        metrics = compare_placements(drawing, [], intersects, orthogonal_path)['baseline']
        candidate = {'name': seed['name'] + f' + compact_corridor:{index}', **planned, 'metrics': metrics}
        inspect_candidate(candidate)
        candidates.append(candidate)
        if improves(candidate) and rank(metrics) < rank(seed['metrics']):
            improved.append(candidate)

    if improved:
        seed = min(improved, key=lambda c: rank(c['metrics']))
        name = seed['name'] + ' + tighten_spacing'
        for _ in range(2):
            projected = preview_route_state(expanded, seed['moves'], seed.get('junction_moves', {}), seed['routes'])
            trials = spacing_trials(projected, blocks, fixed_refs)
            if not trials:
                break
            closer = compare_placements(projected, trials, intersects, orthogonal_path)['candidates']
            options, compatible = [], {}
            for trial in closer:
                if 'metrics' not in trial:
                    continue
                metrics = trial['metrics']
                old = seed['metrics']
                if any(metrics[key] > old[key] for key in PLACEMENT_METRICS[:-1]) or metrics['length'] >= old['length']:
                    continue
                candidate = {'name': name, **compose_sheet_edits(seed, trial), 'metrics': metrics}
                inspect_candidate(candidate)
                if improves(candidate):
                    options.append(candidate)
                    offsets = {**trial['moves'], **trial.get('junction_moves', {})}
                    if offsets.keys().isdisjoint(compatible):
                        compatible.update(offsets)
            if len(options) >= 2 and compatible:
                draft = {'name': name, 'moves': compatible, 'sheet_routing': True}
                joint, = compare_placements(projected, [draft], intersects, orthogonal_path)['candidates']
                if 'metrics' in joint and all(joint['metrics'][key] <= seed['metrics'][key]
                                               for key in PLACEMENT_METRICS[:-1]):
                    candidate = {'name': name, **compose_sheet_edits(seed, joint), 'metrics': joint['metrics']}
                    inspect_candidate(candidate)
                    if improves(candidate):
                        options.append(candidate)
            minimum = max(90, seed['metrics']['length'] * 0.05)
            options = [option for option in options if seed['metrics']['length'] - option['metrics']['length'] >= minimum]
            if not options:
                break
            seed = min(options, key=lambda c: rank(c['metrics']))
        if seed['name'] == name:
            candidates.append(seed)
            improved.append(seed)
    ranked = sorted(improved, key=lambda c: (rank(c['metrics']), len(c['moves'])))
    best = ranked[0] if ranked else None
    return {'baseline': baseline, 'recommended': best['name'] if best else None,
            'ranked': [candidate['name'] for candidate in ranked],
            'candidates': candidates, 'page': expanded['page']['bounds']}, sheet_edit
