"""Shorten orthogonal routes without changing their electrical endpoints."""

from collections import defaultdict, deque
from functools import lru_cache
from itertools import chain, combinations, pairwise
from statistics import median


def direction(a, b):
    return tuple((y > x) - (y < x) for x, y in zip(a, b))


def normalized(points):
    result = []
    for point in points:
        if result and point == result[-1]:
            continue
        if len(result) > 1 and direction(result[-2], result[-1]) == direction(result[-1], point):
            result.pop()
        result.append(point)
    return result


def score(points):
    directions = [direction(a, b) for a, b in pairwise(points)]
    length = sum(abs(a[0] - b[0]) + abs(a[1] - b[1]) for a, b in pairwise(points))
    return sum(a != b for a, b in pairwise(directions)), length


def rectangle(a, b):
    return min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])


def contacts(points, wire, others):
    """Count crossings, touches and overlaps, ignoring shared native endpoints."""
    endpoints = {(end['x'], end['y']): end['id'] for end in (wire['source'], wire['target'])}
    reached = set()
    for other, path in others:
        shared = {(end['x'], end['y']) for end in (other['source'], other['target'])
                  if endpoints.get((end['x'], end['y'])) == end['id']}
        for a, b in pairwise(points):
            left, top, right, bottom = rectangle(a, b)
            for c, d in pairwise(path):
                l, t, r, u = rectangle(c, d)
                x0, y0, x1, y1 = max(left, l), max(top, t), min(right, r), min(bottom, u)
                if x0 > x1 or y0 > y1:
                    continue
                if x0 == x1 and y0 == y1 and (x0, y0) in shared:
                    continue
                reached.add((other['id'], x0, y0, x1, y1))
    return reached


def alternatives(points):
    start, end = points[0], points[-1]
    sx, sy = start
    ex, ey = end
    yield normalized([start, (sx, ey), end])
    yield normalized([start, (ex, sy), end])
    interior = points[1:-1]
    for x in sorted({p[0] for p in interior} | {round((sx + ex) / 18) * 9}):
        yield normalized([start, (x, sy), (x, ey), end])
    for y in sorted({p[1] for p in interior} | {round((sy + ey) / 18) * 9}):
        yield normalized([start, (sx, y), (ex, y), end])


def shifted_segments(path):
    for index in range(1, len(path) - 2):
        a, b = path[index:index + 2]
        axis = int(a[0] != b[0])
        for offset in (-27, -18, -9, 9, 18, 27):
            moved = [tuple(value + (offset if i == axis else 0) for i, value in enumerate(point))
                     for point in (a, b)]
            yield normalized([*path[:index], *moved, *path[index + 2:]])


def route_quality(path, hits, distinct):
    overlaps = sum(x1 - x0 + y1 - y0 for other, x0, y0, x1, y1 in hits if other in distinct)
    crossings = sum(x0 == x1 and y0 == y1 for _, x0, y0, x1, y1 in hits)
    return overlaps, crossings, *score(path)


def proper_crossing(a, b, x, y):
    def axes(path):
        return {axis for p, q in pairwise(path) for axis in (0, 1)
                if p[1-axis] == q[1-axis] == (x, y)[1-axis]
                and min(p[axis], q[axis]) < (x, y)[axis] < max(p[axis], q[axis])}
    return any(i != j for i in axes(a) for j in axes(b))


def body_keepouts(bodies, clearance=9):
    return {ident: {'left': box['left'] - clearance, 'top': box['top'] - clearance,
                    'right': box['right'] + clearance, 'bottom': box['bottom'] + clearance}
            for ident, box in bodies.items()}


def symbol_pin_keepouts(parts, bodies, moves=None):
    leads = []
    for part in parts:
        ident = part.get('id')
        if ident not in bodies:
            continue
        dx, dy = moves.get(ident, (0, 0)) if moves else (0, 0)
        body = bodies[ident]
        for pin in part.get('pins', ()):
            x, y = pin['x'] + dx, pin['y'] + dy
            if pin_exit({'x':x, 'y':y}, body) is None:
                continue
            edge_x, edge_y = min(max(x, body['left']), body['right']), min(max(y, body['top']), body['bottom'])
            left, top, right, bottom = rectangle((x, y), (edge_x, edge_y))
            leads.append((pin.get('id'), ident, dict(left=left-3, top=top-3, right=right+3, bottom=bottom+3)))
    return leads


def body_clearance_contacts(wire, path, bodies, keepouts, intersects, pin_keepouts=()):
    """Find encroachment on a symbol, exempting only an attached pin's outward lead."""
    points = [{'x': x, 'y': y} for x,y in path]
    violations = {}
    for ident, box in keepouts.items():
        exempt = set()
        if len(path) > 1:
            for key, vertex, neighbor, segment in (('source', 0, 1, 0), ('target', -1, -2, len(path)-2)):
                endpoint = wire[key]
                if endpoint.get('component_id') != ident:
                    continue
                outward = pin_exit(endpoint, bodies[ident])
                if outward == direction(path[vertex], path[neighbor]):
                    exempt.add(segment)
        for index,(a,b) in enumerate(pairwise(points)):
            if index not in exempt and intersects(a,b,box):
                violations[ident] = index
                break
    ends = {wire[key]['id'] for key in ('source', 'target')}
    for connector, ident, box in pin_keepouts:
        if connector not in ends and ident not in violations:
            for index,(a,b) in enumerate(pairwise(points)):
                if intersects(a,b,box):
                    violations[ident] = index
                    break
    return violations


def wire_body_clearance_issues(components, wires, intersects):
    by_id = {part['id']: part for part in components}
    bodies = {ident: part['body_bounds'] for ident,part in by_id.items()}
    keepouts = body_keepouts(bodies)
    leads = symbol_pin_keepouts(components, bodies)
    return [{'kind':'wire_symbol_clearance', 'severity':'warning', 'wire':w['id'], 'component':by_id[ident]['ref'],
             'component_id':ident, 'segment':segment, 'minimum_clearance':9, 'suggestion':'simplify_wiring'}
            for w in wires for ident,segment in body_clearance_contacts(
                w, [(p['x'],p['y']) for p in w['points']], bodies, keepouts, intersects, leads).items()]


def plan_shorter_wires(state, intersects, wire_ids=None, extra_routes=None):
    wires = state['wires']
    paths = {w['id']: normalized([(p['x'], p['y']) for p in w['points']]) for w in wires}
    components = state['components']
    changes = []
    page = state['page']['bounds']
    networks = wire_networks(wires)
    pins = {(pin['x'], pin['y']) for part in components for pin in part.get('pins', ())}
    bodies = {part['id']: part['body_bounds'] for part in components if 'id' in part}
    keepouts = body_keepouts(bodies)
    leads = symbol_pin_keepouts(components, bodies)
    obstacles = [c['body_bounds'] for c in components]
    obstacles += [label['bounds'] for c in components for label in c.get('labels', ())
                  if label.get('text') and label.get('bounds')]
    obstacles += [note['bounds'] for note in state.get('texts', ()) if note.get('bounds')]

    for wire in wires:
        wire_id = wire['id']
        if wire_ids is not None and wire_id not in wire_ids:
            continue
        path = paths[wire_id]
        source, target = wire['source'], wire['target']
        if (len(path) < 2 or path[0] != (source['x'], source['y']) or path[-1] != (target['x'], target['y']) or
                any(a[0] != b[0] and a[1] != b[1] for a, b in pairwise(path))):
            continue
        others = [(w, paths[w['id']]) for w in wires if w['id'] != wire_id]
        distinct = {w['id'] for w, _ in others if networks[w['source']['id']] != networks[source['id']]}
        original_contacts = contacts(path, wire, others)
        original_pins = covered_pins(path, pins)
        original_clearance = body_clearance_contacts(wire, path, bodies, keepouts, intersects, leads)
        baseline = (len(original_clearance), *route_quality(path, original_contacts, distinct))
        best = None
        extra = extra_routes.get(wire_id, ()) if extra_routes else ()
        shifts = shifted_segments(path) if any(baseline[:3]) else ()
        for candidate in chain(alternatives(path), shifts, extra):
            if len(candidate) < 2 or any(not (page['left'] <= x <= page['right'] and page['top'] <= y <= page['bottom'])
                                         for x, y in candidate):
                continue
            if candidate[0] != path[0] or candidate[-1] != path[-1] or any(
                a[0] != b[0] and a[1] != b[1] for a, b in pairwise(candidate)
            ):
                continue
            if source.get('component_id') and direction(*candidate[:2]) != direction(*path[:2]):
                continue
            if target.get('component_id') and direction(*candidate[-2:]) != direction(*path[-2:]):
                continue
            points = [{'x': x, 'y': y} for x, y in candidate]
            if any(intersects(a, b, bounds) for a, b in pairwise(points) for bounds in obstacles):
                continue
            if body_clearance_contacts(wire, candidate, bodies, keepouts, intersects, leads):
                continue
            if covered_pins(candidate, pins) - original_pins:
                continue
            hits = contacts(candidate, wire, others)
            quality = (0, *route_quality(candidate, hits, distinct))
            if quality >= baseline or (best and quality >= best[0]):
                continue
            if any(other not in distinct or x0 != x1 or y0 != y1 or
                   not proper_crossing(candidate, paths[other], x0, y0)
                   for other, x0, y0, x1, y1 in hits - original_contacts):
                continue
            best = quality, candidate, points
        if best is not None:
            (_, overlap, crossings, bends, length), paths[wire_id], points = best
            old_clearance, old_overlap, old_crossings, old_bends, old_length = baseline
            changes.append({'wire_id': wire_id, 'points': points, 'bends_before': old_bends,
                            'bends_after': bends, 'length_before': old_length, 'length_after': length,
                            'clearance_violations_before': old_clearance, 'clearance_violations_after': 0,
                            'distinct_overlap_before': old_overlap, 'distinct_overlap_after': overlap,
                            'point_contacts_before': old_crossings, 'point_contacts_after': crossings})
    return changes


LOCAL_ROUTE_METRICS = ('symbol_clearance_violations', 'distinct_overlap', 'point_contacts', 'label_contacts', 'bends', 'length')


def local_channel_paths(path, source_exit, target_exit):
    """Generate nearby orthogonal channels, including both sides of the existing route."""
    start, end = path[0], path[-1]
    yield from alternatives(path)
    yield from shifted_segments(path)
    for lead in (18, 27, 36):
        first = tuple(value + delta*lead for value,delta in zip(start,source_exit))
        last = tuple(value + delta*lead for value,delta in zip(end,target_exit))
        for x in {start[0], end[0], *(p[0] for p in path[1:-1])}:
            for shift in (0, -9, 9, -18, 18, -27, 27):
                lane = x + shift
                yield normalized([start, first, (lane,first[1]), (lane,last[1]), last, end])
        for y in {start[1], end[1], *(p[1] for p in path[1:-1])}:
            for shift in (0, -9, 9, -18, 18, -27, 27):
                lane = y + shift
                yield normalized([start, first, (first[0],lane), (last[0],lane), last, end])


def plan_ordered_wiring(state, pairs, intersects, reserved_columns=()):
    """Route ordered pin pairs together on distinct vertical channels between facing pins."""
    if not 2 <= len(pairs) <= 8:
        raise ValueError('Select 2 to 8 ordered pin pairs')
    parts = state['components']
    wires = state['wires']
    bodies = {p['id']:p['body_bounds'] for p in parts}
    keepouts = body_keepouts(bodies)
    leads = symbol_pin_keepouts(parts,bodies)
    obstacles = [l['bounds'] for part in parts for l in part.get('labels', ()) if l.get('text') and l.get('bounds')]
    obstacles += [note['bounds'] for note in state.get('texts', ()) if note.get('bounds')]
    occupied = {(p['x'],p['y']) for part in parts for p in part.get('pins', ())}
    occupied.update((w[k]['x'],w[k]['y']) for w in wires for k in ('source','target'))
    page = state['page']['bounds']
    members = []
    used = set()

    def endpoint(spec):
        ref,name = spec
        found = [(p,pin) for p in parts if p['ref']==ref for pin in p.get('pins', ()) if pin['name']==name]
        if len(found)!=1 or found[0][1]['id'] in used:
            raise ValueError(f'Unknown, ambiguous or repeated pin: {ref}.{name}')
        part,pin = found[0]
        used.add(pin['id'])
        return {**pin,'ref':ref,'pin':name,'component_id':part['id']}

    for first,last in pairs:
        a,b = endpoint(first),endpoint(last)
        if (a['x']>=b['x'] or pin_exit(a,bodies[a['component_id']])!=(1,0) or
                pin_exit(b,bodies[b['component_id']])!=(-1,0)):
            raise ValueError('Each pair must run left to right between facing horizontal pins')
        match = [w for w in wires if {w['source']['id'],w['target']['id']}=={a['id'],b['id']}]
        if len(match)>1 or (not match and any(w[k]['id'] in {a['id'],b['id']} for w in wires for k in ('source','target'))):
            raise ValueError(f"{a['ref']}.{a['pin']} and {b['ref']}.{b['pin']} must be free pins or one direct wire")
        members.append((a,b,match[0] if match else None))

    replaced = {w['id'] for _,_,w in members if w is not None}
    fixed = [(w,normalized([(p['x'],p['y']) for p in w['points']])) for w in wires if w['id'] not in replaced]
    networks = wire_networks(wires)
    reserved = set(reserved_columns)
    choices = []
    for index,(a,b,old) in enumerate(members):
        proposed = old or {'id':f'pair{index}','source':a,'target':b}
        start,end = (a['x'],a['y']),(b['x'],b['y'])
        candidates = []
        for x in range(((a['x']+17)//9)*9,b['x']-8,9):
            if x in reserved:
                continue
            path = normalized([start,(x,start[1]),(x,end[1]),end])
            if old is not None and old['source']['id'] != a['id']:
                path.reverse()
            coordinates = [dict(x=px,y=py) for px,py in path]
            if any(not (page['left']<=px<=page['right'] and page['top']<=py<=page['bottom']) for px,py in path):
                continue
            if covered_pins(path,occupied)-{start,end} or body_clearance_contacts(proposed,path,bodies,keepouts,intersects,leads):
                continue
            if any(intersects(p,q,box) for p,q in pairwise(coordinates) for box in obstacles):
                continue
            hits = contacts(path,proposed,fixed)
            group = networks.get(a['id'],{a['id']})
            other_paths = {w['id']:(w,p) for w,p in fixed}
            if any(x0!=x1 or y0!=y1 or other_paths[ident][0]['source']['id'] in group or
                   not proper_crossing(path,other_paths[ident][1],x0,y0) for ident,x0,y0,x1,y1 in hits):
                continue
            candidates.append((x,len(hits),proposed,path,coordinates,old))
        choices.append(candidates)

    best = None
    # Keep a bounded set of partial bundles in each column order. Contacts between the
    # newly routed pairs are evaluated here, after they have been assigned separate channels.
    for reverse in (False,True,None):
        if reverse is None and best is not None:
            break
        beam = [((0,0,0),[])]
        for options in choices:
            expanded = []
            for current,routes in beam:
                for option in options:
                    x,hits,proposed,path,_,_ = option
                    if any(item[0]==x for item in routes):
                        continue
                    if routes and (reverse is False and x<routes[-1][0] or reverse is True and x>routes[-1][0]):
                        continue
                    contacts_in_bundle = contacts(path,proposed,[(item[2],item[3]) for item in routes])
                    if any(x0!=x1 or y0!=y1 or not proper_crossing(path,next(item[3] for item in routes
                        if item[2]['id']==ident),x0,y0) for ident,x0,y0,x1,y1 in contacts_in_bundle):
                        continue
                    bends,length = score(path)
                    expanded.append(((current[0]+hits+len(contacts_in_bundle),current[1]+bends,current[2]+length),[*routes,option]))
            beam = sorted(expanded,key=lambda item:(item[0],tuple(route[0] for route in item[1])))[:32]
        if beam and (best is None or beam[0][0]<best[0]):
            best = (*beam[0],reverse)
    if not best:
        return {'reason':'No ordered channels fit the selected pins and reserved columns'}

    costs,routes,column_order = best
    operations,expected,preview = [],[],[]
    for x,_,proposed,path,points,old in routes:
        a,b = proposed['source'],proposed['target']
        if old is None:
            operations.append({'operation':'connect_endpoints','arguments':{'source':{'component_ref':a['ref'],'pin':a['pin']},
                               'target':{'component_ref':b['ref'],'pin':b['pin']},'points':points}})
            expected.append(proposed)
        elif path != normalized([(p['x'],p['y']) for p in old['points']]):
            operations.append({'operation':'route_wire','arguments':{'wire_id':old['id'],'points':points}})
        preview.append({'source':[a['ref'],a['pin']],'target':[b['ref'],b['pin']],'column':x,'points':points})
    return {'routes':preview,'operations':operations,'new_links':expected,
            'column_order':{False:'ascending',True:'descending',None:'mixed'}[column_order],
            'metrics':dict(zip(('point_contacts','bends','length'),costs))}


def plan_local_routing(state, intersects, wire_ids, extra_routes=None, search=True, preserve_pin_clearance=False):
    """Compare combinations of routes with all connector endpoints held fixed."""
    by_id = {wire['id']: wire for wire in state['wires']}
    if search and not 2 <= len(wire_ids) <= 16:
        raise ValueError('Select 2 to 16 wires from one local routing area')
    missing = set(wire_ids) - by_id.keys()
    if missing:
        raise ValueError(f'Unknown wire IDs: {", ".join(sorted(missing))}')
    selected = [ident for ident in by_id if ident in wire_ids]
    positions = {ident: index for index, ident in enumerate(selected)}
    paths = {ident: tuple(normalized([(p['x'],p['y']) for p in w['points']])) for ident,w in by_id.items()}
    fixed = [ident for ident in by_id if ident not in wire_ids]
    networks = wire_networks(state['wires'])
    parts = state['components']
    bodies = {part['id']: part['body_bounds'] for part in parts}
    keepouts = body_keepouts(bodies)
    leads = symbol_pin_keepouts(parts, bodies)
    labels = [l['bounds'] for p in parts for l in p.get('labels', ()) if l.get('text') and l.get('bounds')]
    labels += [note['bounds'] for note in state.get('texts', ()) if note.get('bounds')]
    pins = {(p['x'],p['y']) for part in parts for p in part.get('pins', ())}
    page = state['page']['bounds']
    zero = (0,)*len(LOCAL_ROUTE_METRICS)

    def add(*values):
        return tuple(map(sum,zip(*values))) if values else zero

    @lru_cache(maxsize=None)
    def original_contacts(a, b):
        return contacts(paths[a], by_id[a], [(by_id[b],paths[b])])

    @lru_cache(maxsize=None)
    def interaction(a, route_a, b, route_b):
        hits = contacts(route_a, by_id[a], [(by_id[b],route_b)])
        if not hits:
            return zero
        distinct = networks[by_id[a]['source']['id']] != networks[by_id[b]['source']['id']]
        for _, x0,y0,x1,y1 in hits-original_contacts(a,b):
            if not distinct or x0 != x1 or y0 != y1 or not proper_crossing(route_a,route_b,x0,y0):
                return None
        overlap = sum(x1-x0+y1-y0 for _,x0,y0,x1,y1 in hits if distinct)
        points = sum(x0==x1 and y0==y1 for _,x0,y0,x1,y1 in hits)
        return 0,overlap,points,0,0,0

    def individually(ident, path):
        wire = by_id[ident]
        coordinates = [dict(x=x,y=y) for x,y in path]
        violations = len(body_clearance_contacts(wire,path,bodies,keepouts,intersects,leads))
        label_hits = sum(any(intersects(a,b,box) for a,b in pairwise(coordinates)) for box in labels)
        outside = [interaction(ident,path,other,paths[other]) for other in fixed]
        if None in outside:
            return None
        return add((violations,0,0,label_hits,*score(path)),*outside)

    baseline = add(*(individually(ident,paths[ident]) for ident in selected),
                   *(interaction(a,paths[a],b,paths[b]) for a,b in combinations(selected,2)))
    if not search:
        return dict(zip(LOCAL_ROUTE_METRICS,baseline))

    options = {}
    for ident in selected:
        wire, old = by_id[ident],paths[ident]
        endpoint_coords = tuple((wire[key]['x'],wire[key]['y']) for key in ('source','target'))
        choices = {old:individually(ident,old)}
        if (len(old) > 1 and (old[0],old[-1]) == endpoint_coords and
                all(a[0] == b[0] or a[1] == b[1] for a,b in pairwise(old))):
            exits = []
            for key,index,neighbor in (('source',0,1),('target',-1,-2)):
                terminal = wire[key]
                body = bodies.get(terminal.get('component_id'))
                exits.append((pin_exit(terminal,body) or direction(old[index],old[neighbor])) if body else (0,0))
            extra = (extra_routes or {}).get(ident, ())
            routes = dict.fromkeys(tuple(normalized(route)) for route in chain(local_channel_paths(old,*exits),extra))
            occupied = covered_pins(old,pins)
            for path in routes.keys() - choices.keys():
                if (len(path) < 2 or (path[0],path[-1]) != endpoint_coords or
                        any(a[0] != b[0] and a[1] != b[1] for a,b in pairwise(path)) or self_intersects(path)):
                    continue
                if any(not (page['left'] <= x <= page['right'] and page['top'] <= y <= page['bottom']) for x,y in path):
                    continue
                if (exits[0] != (0,0) and direction(*path[:2]) != exits[0] or
                        exits[1] != (0,0) and direction(path[-1],path[-2]) != exits[1]):
                    continue
                if preserve_pin_clearance and len(path) > 2 and any(
                    outward != (0, 0) and score((path[end], path[next_point]))[1] < 18
                    and (len(old) == 2 or score((old[end], old[next_point]))[1] >= 18)
                    for outward, end, next_point in zip(exits, (0, -1), (1, -2))
                ):
                    continue
                if covered_pins(path,pins)-occupied or body_clearance_contacts(wire,path,bodies,keepouts,intersects,leads):
                    continue
                value = individually(ident,path)
                if value is not None:
                    choices[path] = value
        # Keep the existing route available even if several alternatives score better in isolation.
        best = sorted(choices,key=lambda path:(choices[path],path))[:16]
        options[ident] = [(path,choices[path]) for path in dict.fromkeys([old,*best])]

    completed = {}
    by_constraints = sorted(selected,key=lambda ident:len(options[ident]))

    def candidate_key(value, planned):
        return (value, sum(path != paths[ident] for ident,path in planned),
                tuple((positions[ident], path) for ident, path in planned))

    for order in (by_constraints, by_constraints[::-1]):
        beam = [(zero,())]
        for ident in order:
            next_beam = []
            for previous, planned in beam:
                for path,value in options[ident]:
                    costs = [interaction(ident,path,other,other_path) for other,other_path in planned]
                    if None not in costs:
                        next_beam.append((add(previous,value,*costs),(*planned,(ident,path))))
            beam = sorted(next_beam,key=lambda candidate: candidate_key(*candidate))[:12]
        for value, planned in beam:
            if value < baseline:
                completed[tuple(sorted(planned, key=lambda item: positions[item[0]]))] = value

    alternatives_found = []
    for planned,value in sorted(completed.items(),key=lambda item: candidate_key(item[1],item[0]))[:3]:
        changes = [{'wire_id':ident,'points':[dict(x=x,y=y) for x,y in path]}
                   for ident,path in planned if path != paths[ident]]
        alternatives_found.append({'metrics':dict(zip(LOCAL_ROUTE_METRICS,value)), 'changes':changes})
    return {'baseline':dict(zip(LOCAL_ROUTE_METRICS,baseline)), 'alternatives':alternatives_found}


def covered_pins(path, pins):
    return {(x, y) for a, b in pairwise(path) for x, y in pins
            if min(a[0], b[0]) <= x <= max(a[0], b[0]) and min(a[1], b[1]) <= y <= max(a[1], b[1])}


def self_intersects(path):
    segments = [rectangle(a, b) for a, b in pairwise(path)]
    directions = (direction(a, b) for a, b in pairwise(path))
    if any(a == (-b[0], -b[1]) for a, b in pairwise(directions)):
        return True
    return any(max(l, a) <= min(r, c) and max(t, b) <= min(u, d)
               for i, (l, t, r, u) in enumerate(segments) for a, b, c, d in segments[i+2:])


def overlaps(a, b):
    return (max(a['left'], b['left']) < min(a['right'], b['right']) and
            max(a['top'], b['top']) < min(a['bottom'], b['bottom']))


def inside(box, page):
    return (page['left'] <= box['left'] <= box['right'] <= page['right'] and
            page['top'] <= box['top'] <= box['bottom'] <= page['bottom'])


def translated(box, dx, dy):
    return {k: box[k] + delta for keys, delta in ((('left', 'right'), dx), (('top', 'bottom'), dy)) for k in keys}


PLACEMENT_METRICS = ('wrong_pin_approaches', 'distinct_overlap', 'body_clearance_contacts',
                     'tight_pin_turns', 'point_contacts', 'label_contacts', 'bends', 'length')


def pin_exit(end, body):
    """Derive an outward lead from a pin outside or on one edge of a symbol body."""
    x, y = end['x'], end['y']
    if body['top'] <= y <= body['bottom']:
        if x < body['left'] or x == body['left'] and body['top'] < y < body['bottom']:
            return -1, 0
        if x > body['right'] or x == body['right'] and body['top'] < y < body['bottom']:
            return 1, 0
    if body['left'] <= x <= body['right']:
        if y < body['top'] or y == body['top'] and body['left'] < x < body['right']:
            return 0, -1
        if y > body['bottom'] or y == body['bottom'] and body['left'] < x < body['right']:
            return 0, 1
    return None


def module_placement_proposals(state, refs):
    """Recognize parallel three-way branches between two selected stages and an indicator bank."""
    parts = {part['ref']: part for part in state['components'] if part['ref'] in refs}
    if len(parts) != len(refs):
        raise ValueError('Unknown or ambiguous module component reference')
    branches = defaultdict(list)
    links = defaultdict(set)
    for wire in state['wires']:
        a, b = wire['source'], wire['target']
        for pin, other in ((a, b), (b, a)):
            if not pin.get('ref') and not pin.get('component_id'):
                branches[pin['id']].append((pin, other, wire['id']))
            elif pin.get('ref') in parts and other.get('ref') in parts:
                links[pin['ref']].add(other['ref'])

    groups = defaultdict(list)
    for entries in branches.values():
        endpoints = {entry[1].get('ref') for entry in entries}
        if len(entries) != 3 or len(endpoints) != 3 or not endpoints <= parts.keys():
            continue
        sides = {pin_exit(pin, parts[pin['ref']]['body_bounds']): (joint, pin, wire_id)
                 for joint, pin, wire_id in entries}
        if len(sides) != 3 or (1, 0) not in sides or (-1, 0) not in sides:
            continue
        for side in ((0, 1), (0, -1)):
            if side in sides:
                first, last, tap = (sides[key] for key in ((1, 0), (-1, 0), side))
                groups[first[1]['ref'], last[1]['ref'], side].append((first, last, tap))

    choices = []
    for (driver, receiver, side), lanes in groups.items():
        if len(lanes) < 2 or len({lane[2][1]['ref'] for lane in lanes}) != len(lanes):
            continue
        if len({lane[1][1]['id'] for lane in lanes}) != len(lanes):
            continue
        choices.append((driver, receiver, side, sorted(lanes, key=lambda lane: lane[0][1]['y'])))
    if not choices:
        return [], []
    driver, receiver, _, lanes = max(choices, key=lambda item: len(item[3]))

    def snap(value):
        return round(value / 9) * 9

    taps = [lane[2][1] for lane in lanes]
    tap_refs = {pin['ref'] for pin in taps}
    pitch = max(36, snap(max(parts[pin['ref']]['body_bounds']['right'] - parts[pin['ref']]['body_bounds']['left']
                            for pin in taps) + 18))
    row_start = snap(median(pin['x'] for pin in taps) - pitch * (len(taps) - 1) / 2)
    tap_y = snap(median(pin['y'] for pin in taps))
    align_y = snap(median(first[1]['y'] - last[1]['y'] for first, last, _ in lanes))
    start_x = lanes[0][0][1]['x']
    end_x = lanes[0][1][1]['x']
    summaries = [{'driver': driver, 'driver_pin': first[1]['pin'], 'receiver': receiver,
                  'receiver_pin': last[1]['pin'], 'tap': tap[1]['ref'],
                  'junction_id': first[0]['id'], 'wire_ids': [first[2], last[2], tap[2]]}
                 for first, last, tap in lanes]
    proposals = []
    for name, spacing, inside_corridor, reverse, receiver_dy in (
        ('align_stages', 18, False, False, align_y),
        ('raise_driver_stage', 18, False, False, 0),
        ('ordered_indicator_bank', pitch, True, False, align_y),
        ('compact_indicator_bank', max(27, pitch - 9), True, False, 0),
        ('ordered_bank_lower_stages', pitch, True, False, align_y + 36),
        ('ordered_indicator_bank_reversed', pitch, True, True, align_y),
    ):
        width = spacing * (len(lanes) - 1)
        spread = max(0, snap(width + 54 - (end_x - start_x)))
        left_shift = snap(spread / 2)
        moves = {driver: (-left_shift, receiver_dy - align_y), receiver: (spread - left_shift, receiver_dy)}
        joint_start = snap((start_x + end_x + spread - 2*left_shift - width) / 2)
        tracks = {}
        for index, (first, last, tap) in enumerate(lanes):
            joint, tap_pin = first[0], tap[1]
            slot = len(lanes) - 1 - index if reverse else index
            # Spread fork points across the corridor so each tap has a separate vertical lane.
            x = joint_start + slot * spacing
            y = last[1]['y'] + receiver_dy
            moves[joint['id']] = (x - joint['x'], y - joint['y'])
            tap_ref = tap_pin['ref']
            if inside_corridor:
                tap_dx = x - tap_pin['x']
                tap_dy = tap_y - tap_pin['y']
            else:
                tap_dx = row_start + index * pitch - tap_pin['x']
                tap_dy = tap_y - tap_pin['y']
            moves[tap_ref] = (tap_dx, tap_dy)
            mates = links[tap_ref] - tap_refs - {driver, receiver}
            if len(mates) == 1:
                mate, = mates
                if links[mate] == {tap_ref}:
                    moves[mate] = (tap_dx, tap_dy)
            tracks[tap[2]] = x
        if not any(proposal['moves'] == moves for proposal in proposals):
            proposals.append({'name': name, 'moves': moves, 'tracks': tracks,
                              'lane_wires': [wire_id for summary in summaries for wire_id in summary['wire_ids']]})
    return proposals, summaries


def compare_placements(state, proposals, intersects, orthogonal_path, module_refs=None, check_layout=None):
    """Rank explicit translations using estimated routes and unchanged native connector IDs."""
    page = state['page']['bounds']
    wires = state['wires']
    parts = state['components']
    networks = wire_networks(wires)
    original = {w['id']: normalized([(p['x'], p['y']) for p in w['points']]) for w in wires}
    original_bodies = {part['id']: part['body_bounds'] for part in parts}
    notes = [note['bounds'] for note in state.get('texts', ()) if note.get('bounds')]
    by_ref = defaultdict(list)
    for part in parts:
        by_ref[part['ref']].append(part['id'])
    junctions = {end['id']: (end['x'], end['y']) for wire in wires for key in ('source', 'target')
                 for end in [wire[key]] if not end.get('component_id') and not end.get('ref')}
    focus = {wire['id'] for wire in wires if module_refs and
             any(wire[key].get('ref') in module_refs for key in ('source', 'target'))}

    def points(path):
        return [dict(x=x, y=y) for x, y in path]

    def crosses(path, box):
        return any(intersects(a, b, box) for a, b in pairwise(points(path)))

    def labels(moves):
        return notes + [translated(label['bounds'], *moves.get(part['id'], (0, 0)))
                        for part in parts for label in part.get('labels', ()) if label.get('text') and label.get('bounds')]

    def quality(wire, path, others, boxes, bodies, keepout, leads):
        different = {w['id'] for w, _ in others if networks[w['source']['id']] != networks[wire['source']['id']]}
        overlap, crossings, bends, length = route_quality(path, contacts(path, wire, others), different)
        wrong = tight = 0
        if len(path) > 1:
            for key, index, neighbor in (('source', 0, 1), ('target', -1, -2)):
                pid = wire[key].get('component_id')
                if pid in bodies:
                    outward = pin_exit(wire[key], bodies[pid])
                    wrong += outward is not None and direction(path[index], path[neighbor]) != outward
                    tight += outward is not None and len(path) > 2 and score((path[index], path[neighbor]))[1] < 18
        near = body_clearance_contacts(wire, path, bodies, keepout, intersects, leads)
        return wrong, overlap, len(near), tight, crossings, sum(crosses(path, box) for box in boxes), bends, length

    def metrics(routed, boxes, bodies, leads, selected=None):
        keepout = body_keepouts(bodies)
        # Count a wire pair once. Module totals also count contacts with wires outside the selected block.
        ordered = routed if selected is None else sorted(routed, key=lambda item: item[0]['id'] in selected)
        scores = [quality(wire, path, ordered[:index], boxes, bodies, keepout, leads)
                  for index, (wire, path) in enumerate(ordered) if selected is None or wire['id'] in selected]
        return dict(zip(PLACEMENT_METRICS, map(sum, zip(*scores)))) if scores else dict.fromkeys(PLACEMENT_METRICS, 0)

    initial = [(wire, original[wire['id']]) for wire in wires]
    initial_leads = symbol_pin_keepouts(parts, original_bodies)
    initial_keepouts = body_keepouts(original_bodies)
    initial_near = {wire['id']: set(body_clearance_contacts(wire, path, original_bodies, initial_keepouts,
                                                             intersects, initial_leads)) for wire,path in initial}
    baseline = metrics(initial, labels({}), original_bodies, initial_leads)
    module_baseline = metrics(initial, labels({}), original_bodies, initial_leads, focus) if focus else None
    results = []
    for proposal in proposals:
        moves = {}
        junction_moves = {}
        for ref, delta in proposal['moves'].items():
            if any(value % 9 for value in delta):
                raise ValueError('Placement offsets must be multiples of 9 sheet units')
            if ref in junctions:
                if any(delta):
                    junction_moves[ref] = delta
            elif len(by_ref[ref]) != 1:
                raise ValueError(f'Unknown or ambiguous component reference or junction ID: {ref}')
            elif any(delta):
                moves[by_ref[ref][0]] = delta
        result = {'name': proposal['name'], 'moves': {ref: delta for ref, delta in proposal['moves'].items()
                                                    if ref not in junctions}}
        if junction_moves:
            result['junction_moves'] = junction_moves
        results.append(result)
        bodies = [(part, translated(part['body_bounds'], *moves.get(part['id'], (0, 0)))) for part in parts]
        blocked = [p['ref'] for p, body in bodies if p['id'] in moves and not inside(body, page)]
        blocked.extend(f"{a['ref']} / {b['ref']}" for (a, first), (b, second) in combinations(bodies, 2)
                       if (a['id'] in moves or b['id'] in moves) and overlaps(first, second))
        shifted_junctions = {key: (x + dx, y + dy) for key, (x, y) in junctions.items()
                             for dx, dy in [junction_moves.get(key, (0, 0))]}
        occupied = {(pin['x'] + dx, pin['y'] + dy) for part in parts for pin in part.get('pins', ())
                    for dx, dy in [moves.get(part['id'], (0, 0))]}
        for jid in junction_moves:
            x, y = shifted_junctions[jid]
            if (not (page['left'] <= x <= page['right'] and page['top'] <= y <= page['bottom'])
                    or (x, y) in occupied or list(shifted_junctions.values()).count((x, y)) > 1
                    or any(body['left'] < x < body['right'] and body['top'] < y < body['bottom']
                           for _, body in bodies)):
                blocked.append(jid)
        if blocked:
            result['placement_conflicts'] = blocked
            continue
        obstacles = [body for _, body in bodies]
        body_by_id = {part['id']: body for part, body in bodies}
        keepout = body_keepouts(body_by_id)
        symbol_leads = symbol_pin_keepouts(parts, body_by_id, moves)
        label_boxes = labels(moves)
        routed, affected = [], []
        for wire in wires:
            path = original[wire['id']]
            if wire['id'] in proposal.get('reroute', ()) or any(
                   wire[key].get('component_id') in moves or wire[key]['id'] in junction_moves
                   for key in ('source', 'target')) or set(body_clearance_contacts(
                       wire, path, body_by_id, keepout, intersects, symbol_leads)) - initial_near[wire['id']]:
                affected.append(wire)
            else:
                routed.append((wire, path))
        pins = occupied | set(shifted_junctions.values())
        changed, failed = {}, []
        lane_wires = set(proposal.get('lane_wires', ()))
        tracks = proposal.get('tracks', {})
        route_options = {}

        def misses_track(wid, path):
            track = tracks.get(wid)
            return int(track is not None and not any(a[0] == b[0] == track for a, b in pairwise(path)))

        def choose(wire, options, neighbors):
            def rank(path):
                value = quality(wire, path, neighbors, label_boxes, body_by_id, keepout, symbol_leads)
                return (*value[:6], misses_track(wire['id'], path), *value[6:])
            return min(options, key=rank)

        if lane_wires:
            # Lay out the lane spines, then their tap branches, before incidental routes.
            affected.sort(key=lambda wire: (wire['id'] not in lane_wires, wire['id'] in tracks))
        for wire in affected:
            wid = wire['id']
            old = original[wid]
            planned = dict(wire)
            ends, leads, deltas = [], [], []
            for key, index, neighbor in (('source', 0, 1), ('target', -1, -2)):
                end = wire[key]
                dx, dy = junction_moves.get(end['id'], moves.get(end.get('component_id'), (0, 0)))
                deltas.append((dx, dy))
                x, y = end['x'] + dx, end['y'] + dy
                planned[key] = {**end, 'x': x, 'y': y}
                ends.append((x, y))
                pid = end.get('component_id')
                hx, hy = (pin_exit(planned[key], body_by_id[pid]) or direction(old[index], old[neighbor])
                          if len(old) > 1 and pid in body_by_id else (0, 0))
                leads.append((x + 18*hx, y + 18*hy))
            start, end = ends
            first, last = leads
            candidates = list(alternatives([first, *old[1:-1], last]))
            try:
                middle = orthogonal_path(*points(leads), obstacles)
                candidates.append([(p['x'], p['y']) for p in middle])
            except ValueError:
                pass
            trials = [*alternatives([start, *old[1:-1], end]),
                      *(normalized([start, *middle, end]) for middle in candidates)]
            if proposal.get('sheet_routing'):
                if deltas[0] == deltas[1]:
                    dx, dy = deltas[0]
                    trials.append([(x + dx, y + dy) for x, y in old])
                middle_x = round((first[0] + last[0]) / 18) * 9
                middle_y = round((first[1] + last[1]) / 18) * 9
                for x in {middle_x, min(first[0], last[0]) - 18, max(first[0], last[0]) + 18}:
                    trials.append(normalized([start, first, (x, first[1]), (x, last[1]), last, end]))
                for y in {middle_y, min(first[1], last[1]) - 18, max(first[1], last[1]) + 18}:
                    trials.append(normalized([start, first, (first[0], y), (last[0], y), last, end]))
            track = tracks.get(wid)
            if track is not None:
                # A joint and its tap share a reserved vertical track when the geometry permits it.
                trials.append(normalized([start, first, (track, first[1]), (track, last[1]), last, end]))
            trials = dict.fromkeys(tuple(path) for path in chain(trials, *(shifted_segments(path) for path in trials)))
            feasible = []
            for path in trials:
                if len(path) < 2 or any(not (page['left'] <= x <= page['right'] and page['top'] <= y <= page['bottom'])
                                        for x, y in path):
                    continue
                if any(a[0] != b[0] and a[1] != b[1] for a, b in pairwise(path)):
                    continue
                if (first != start and direction(*path[:2]) != direction(start, first) or
                        last != end and direction(path[-1], path[-2]) != direction(end, last)):
                    continue
                if self_intersects(path) or covered_pins(path, pins) - set(ends) or any(crosses(path, box) for box in obstacles):
                    continue
                if set(body_clearance_contacts(planned, path, body_by_id, keepout, intersects, symbol_leads)) - initial_near[wid]:
                    continue
                feasible.append(path)
            if not feasible:
                failed.append(wid)
                continue
            path = choose(planned, feasible, routed)
            changed[wid] = len(routed)
            routed.append((planned, path))
            if lane_wires:
                route_options[wid] = feasible
        result['unrouted_wires'] = failed
        if not failed:
            # Recheck every affected route against the completed module routing.
            for wid, options in reversed(list(route_options.items())):
                index = changed[wid]
                wire, current = routed[index]
                route = choose(wire, chain((current,), options), routed[:index] + routed[index+1:])
                routed[index] = wire, route
            routes = [{'wire_id': wid, 'points': points(routed[index][1])} for wid, index in changed.items()]
            result.update(metrics=metrics(routed, label_boxes, body_by_id, symbol_leads), routes=routes)
            if tracks:
                result['reserved_tap_tracks'] = len(tracks)
                result['tap_track_misses'] = sum(misses_track(wid, routed[index][1]) for wid, index in changed.items())
                if check_layout:
                    moved_parts = []
                    for part, body in bodies:
                        dx, dy = moves.get(part['id'], (0, 0))
                        moved_parts.append({**part, 'body_bounds': body,
                                            'labels': [{**label, 'bounds': translated(label['bounds'], dx, dy)}
                                                       if label.get('bounds') else label for label in part.get('labels', ())]})
                    moved_wires = [{**wire, 'points': points(path)} for wire, path in routed]
                    preview = {**state, 'components': moved_parts, 'wires': moved_wires}
                    issues = check_layout(moved_parts, moved_wires)['issues']
                    result['label_moves'] = label_move_hints(preview, issues, intersects, module_refs)
            if focus:
                measured = metrics(routed, label_boxes, body_by_id, symbol_leads, focus)
                result['module_metrics'] = measured
                result['module_delta'] = {key: measured[key] - module_baseline[key] for key in PLACEMENT_METRICS}

    def rank(item):
        return tuple(item['metrics'][key] for key in PLACEMENT_METRICS)

    ranked = sorted((item for item in results if 'metrics' in item), key=rank)
    automatic = any(proposal.get('lane_wires') for proposal in proposals)
    recommended = next((item['name'] for item in ranked if rank(item) < rank({'metrics': baseline}) and (
        not automatic or item['metrics']['distinct_overlap'] <= baseline['distinct_overlap']
    )), None)
    return {'baseline': baseline, 'recommended': recommended, 'ranked': [item['name'] for item in ranked], 'candidates': results,
            **({'module_baseline': module_baseline} if focus else {})}


def preview_route_state(state, moves, junction_moves, routes):
    """Project native translations into a throwaway state for scoring the same routing scope."""
    def move_point(point, delta):
        moved = {**point, 'x': point['x'] + delta[0], 'y': point['y'] + delta[1]}
        graphic = point.get('graphic_position')
        if graphic:
            moved['graphic_position'] = {**graphic, 'x': graphic['x'] + delta[0], 'y': graphic['y'] + delta[1]}
        return moved

    deltas = {part['id']: moves.get(part['ref'], (0, 0)) for part in state['components']}
    parts = []
    for part in state['components']:
        dx, dy = deltas[part['id']]
        parts.append({**part, 'body_bounds': translated(part['body_bounds'], dx, dy),
                      **({'bounds': translated(part['bounds'], dx, dy)} if part.get('bounds') else {}),
                      'pins': [move_point(pin, (dx, dy)) for pin in part.get('pins', ())],
                      'labels': [{**label, 'bounds': translated(label['bounds'], dx, dy)}
                                 if label.get('bounds') else label for label in part.get('labels', ())]})
    paths = {route['wire_id']: route['points'] for route in routes}
    wires = []
    for wire in state['wires']:
        endpoints = {key: move_point(wire[key], junction_moves.get(wire[key]['id'], deltas.get(
            wire[key].get('component_id'), (0, 0)))) for key in ('source', 'target')}
        wires.append({**wire, **endpoints, 'points': paths.get(wire['id'], wire['points'])})
    return {**state, 'components': parts, 'wires': wires}


def local_placement_candidates(state, refs, allowed_junctions):
    """Try a few grid-aligned shifts and direct pin alignment in a selected block."""
    parts = {p['ref']: p for p in state['components'] if p['ref'] in refs}
    proposals = []
    if len(refs) == 2:
        left, right = sorted(refs, key=lambda ref: (parts[ref]['body_bounds']['left'], ref))
        proposals.extend(({'name':'wider', 'moves':{left:(-36,0), right:(36,0)}},
                          {'name':'closer', 'moves':{left:(18,0), right:(-18,0)}}))
        for wire in state['wires']:
            a,b = wire['source'],wire['target']
            if (a.get('ref'),b.get('ref')) == (right,left):
                a,b = b,a
            if (a.get('ref'),b.get('ref')) != (left,right):
                continue
            if pin_exit(a,parts[left]['body_bounds']) != (1,0) or pin_exit(b,parts[right]['body_bounds']) != (-1,0):
                continue
            dy = b['y'] - a['y']
            if dy and dy % 9 == 0 and abs(dy) <= 72:
                proposals.extend(({'name':'align_left', 'moves':{left:(0,dy)}},
                                  {'name':'align_right', 'moves':{right:(0,-dy)}}))
                half = round(dy/18)*9
                proposals.append({'name':'align_both', 'moves':{left:(0,half),right:(0,half-dy)}})
                break
    for ref in sorted(refs):
        for name,delta in (('left',(-36,0)),('right',(36,0)),('up',(0,-36)),('down',(0,36))):
            proposals.append({'name':f'{ref}_{name}', 'moves':{ref:delta}})

    adjacent = defaultdict(set)
    for wire in state['wires']:
        a,b = wire['source'],wire['target']
        for endpoint,other in ((a,b),(b,a)):
            if endpoint['id'] in allowed_junctions and other.get('ref') in refs:
                adjacent[endpoint['id']].add(other['ref'])
    for proposal in proposals:
        for ident,names in adjacent.items():
            deltas = {proposal['moves'].get(ref,(0,0)) for ref in names}
            if len(deltas) == 1:
                delta, = deltas
                if any(delta):
                    proposal['moves'][ident] = delta
    unique = {}
    for proposal in proposals:
        unique.setdefault(tuple(sorted(proposal['moves'].items())), proposal)
    return list(unique.values())


def plan_local_placement(state, intersects, orthogonal_path, movable_refs, movable_junctions=(),
                         extra_wire_ids=(), candidates=None):
    """Compare a fixed route plan and bounded placement trials over the same wire scope."""
    refs, junctions = set(movable_refs), set(movable_junctions)
    refs_in_state = [p['ref'] for p in state['components']]
    if not 1 <= len(refs) <= 3 or any(refs_in_state.count(ref) != 1 for ref in refs):
        raise ValueError('Select 1 to 3 unique movable component references')
    endpoints = {w[key]['id']:w[key] for w in state['wires'] for key in ('source','target')}
    if any(j not in endpoints or endpoints[j].get('ref') or endpoints[j].get('component_id') for j in junctions):
        raise ValueError('Provide current native junction IDs')
    scope = set(extra_wire_ids)
    scope.update(w['id'] for w in state['wires'] if any(w[key].get('ref') in refs or w[key]['id'] in junctions
                                                      for key in ('source','target')))
    if candidates is not None and (not 1 <= len(candidates) <= 16 or
                                   any(set(moves)-refs-junctions for moves in candidates.values())):
        raise ValueError('Provide 1 to 16 candidates that move only explicitly unlocked objects')
    drafts = ([{'name':name,'moves':moves} for name,moves in candidates.items()] if candidates is not None else
              local_placement_candidates(state, refs, junctions))
    current = plan_local_routing(state, intersects, scope)
    global_ids = {w['id'] for w in state['wires']}
    baseline_global = plan_local_routing(state, intersects, global_ids, search=False)

    def rank(metrics):
        return tuple(metrics[key] for key in LOCAL_ROUTE_METRICS)

    def conflicts(drawing):
        clearance = {(i['wire'],i['component_id']) for i in wire_body_clearance_issues(
            drawing['components'], drawing['wires'], intersects)}
        overlaps = {(frozenset(i['wire_ids']),tuple((p['x'],p['y']) for p in i['points']))
                    for i in wire_overlaps(drawing['wires']) if i['kind']=='distinct_wires_overlap'}
        networks = wire_networks(drawing['wires'])
        paths = [(w, normalized([(p['x'],p['y']) for p in w['points']])) for w in drawing['wires']]
        touches = {(frozenset((a['id'],b['id'])),x0,y0) for (a,pa),(b,pb) in combinations(paths,2)
                   if (a['id'] in scope or b['id'] in scope)
                   and networks[a['source']['id']] != networks[b['source']['id']]
                   for _,x0,y0,x1,y1 in contacts(pa,a,[(b,pb)])
                   if x0 == x1 and y0 == y1 and not proper_crossing(pa,pb,x0,y0)}
        return clearance,overlaps,touches

    def measure(drawing):
        return {'local':plan_local_routing(drawing,intersects,scope,search=False),
                'sheet':plan_local_routing(drawing,intersects,global_ids,search=False)}

    original_conflicts = conflicts(state)
    fixed = current['alternatives'][0] if current['alternatives'] else {'changes':[], 'metrics':current['baseline']}
    fixed_state = preview_route_state(state,{}, {}, fixed['changes'])
    fixed_metrics = {'local':fixed['metrics'], 'sheet':plan_local_routing(fixed_state,intersects,global_ids,search=False)}
    compared = compare_placements(state, drafts, intersects, orthogonal_path)['candidates']
    results = []
    for placement in compared:
        candidate = {key:value for key,value in placement.items() if key not in ('metrics','routes')}
        results.append(candidate)
        if 'routes' not in placement:
            continue
        if any(route['wire_id'] not in scope for route in placement['routes']):
            candidate['reason'] = 'Placement would require rerouting a wire outside the selected scope'
            continue
        moves, junction_moves = placement['moves'], placement.get('junction_moves',{})
        projected = preview_route_state(state,moves,junction_moves,placement['routes'])
        replanned = plan_local_routing(projected,intersects,scope)
        for changes in [option['changes'] for option in replanned['alternatives']] + [[]]:
            final_by_id = {route['wire_id']:route for route in chain(placement['routes'],changes)}
            routes = list(final_by_id.values())
            drawing = preview_route_state(state,moves,junction_moves,routes)
            if any(after-before for after,before in zip(conflicts(drawing),original_conflicts)):
                continue
            candidate.update(metrics=measure(drawing),routes=routes)
            break
        else:
            candidate['reason'] = 'Placement would introduce new symbol-clearance violations, overlaps or ambiguous wire touches'
    feasible = [item for item in results if 'metrics' in item and rank(item['metrics']['local']) < rank(fixed_metrics['local'])]
    best = min(feasible,key=lambda item:(rank(item['metrics']['local']),rank(item['metrics']['sheet']),
               len(item['moves'])+len(item.get('junction_moves',{}))),default=None)
    return {'selected_wire_ids':sorted(scope),'baseline':{'local':current['baseline'],'sheet':baseline_global},
            'fixed':{'metrics':fixed_metrics,'routes':fixed['changes']},'candidates':results,
            'recommended':best['name'] if best else None}


def plan_component_group(state, refs, axis, intersects, spacing=None):
    """Preview equally spaced bodies, then common label slots once bodies are in place."""
    parts = state['components']
    group = [part for part in parts if part['ref'] in refs]
    if len(group) < 2 or len(group) != len(refs):
        raise ValueError('Select at least two unique component references')
    if spacing is not None and (spacing <= 0 or spacing % 9):
        raise ValueError('Spacing must be a positive multiple of 9 sheet units')
    main, cross = ('top', 'left') if axis == 'column' else ('left', 'top')
    group.sort(key=lambda part: part['body_bounds'][main])
    starts = [part['body_bounds'][main] for part in group]
    align = median(part['body_bounds'][cross] for part in group)
    pitch = spacing or max(9, round(median(b-a for a, b in pairwise(starts)) / 9) * 9)
    page = state['page']['bounds']
    others = [part for part in parts if part['ref'] not in refs]
    boxes = [part['body_bounds'] for part in others]
    moves = []
    for index, part in enumerate(group):
        box = part['body_bounds']
        along = round((starts[0] + index*pitch - box[main]) / 9) * 9
        across = round((align - box[cross]) / 9) * 9
        dx, dy = (across, along) if axis == 'column' else (along, across)
        moved = translated(box, dx, dy)
        if not inside(moved, page) or any(overlaps(moved, fixed) for fixed in boxes):
            return {'spacing': pitch, 'component_moves': [], 'label_moves': [], 'blocked_by': part['ref']}
        boxes.append(moved)
        if dx or dy:
            moves.append({'component_ref': part['id'], 'ref': part['ref'], 'dx': dx, 'dy': dy})
    result = {'spacing': pitch, 'component_moves': moves, 'label_moves': []}
    if moves:
        return result

    boxes += [note['bounds'] for note in state.get('texts', ()) if note.get('bounds')]
    movable = [(part, label) for part in group for label in part.get('labels', ())
               if label.get('text') and label.get('bounds') and label['kind'] in ('reference', 'value')]
    if not movable:
        return result
    moving_ids = {label['id'] for _, label in movable}
    boxes += [label['bounds'] for part in parts for label in part.get('labels', ())
              if label.get('text') and label.get('bounds') and label['id'] not in moving_ids]
    segments = [segment for wire in state['wires'] for segment in pairwise(wire['points'])]
    shifts = sorted(range(-36, 37, 3), key=abs)
    def slots(kind):
        items = [(part, label['bounds']) for part, label in movable if label['kind'] == kind]
        if not items:
            yield [], []
            return
        width = max(box['right'] - box['left'] for _, box in items)
        height = max(box['bottom'] - box['top'] for _, box in items)
        for side in ('right', 'left', 'above', 'below'):
            for gap in (6, 12, 18, 24):
                for shift in shifts:
                    placed, hints = [], []
                    for part, old in items:
                        body = part['body_bounds']
                        mid_x = (body['left'] + body['right']) / 2
                        mid_y = (body['top'] + body['bottom']) / 2
                        if side in ('right', 'left'):
                            x = body['right'] + gap + width/2 if side == 'right' else body['left'] - gap - width/2
                            y = mid_y + shift
                        else:
                            x = mid_x + shift
                            y = body['top'] - gap - height/2 if side == 'above' else body['bottom'] + gap + height/2
                        x = body['left'] + round(x - body['left'])
                        y = body['top'] + round(y - body['top'])
                        box = translated(old, x - (old['left'] + old['right'])/2, y - (old['top'] + old['bottom'])/2)
                        if not inside(box, page) or any(overlaps(box, fixed) for fixed in chain(boxes, placed)) or any(
                            intersects(a, b, box) for a, b in segments
                        ):
                            break
                        placed.append(box)
                        hints.append({'component_ref': part['id'], 'ref': part['ref'], 'kind': kind, 'x': x, 'y': y})
                    else:
                        yield placed, hints

    reference_slots = list(slots('reference'))
    for value_boxes, values in slots('value'):
        for ref_boxes, references in reference_slots:
            if not any(overlaps(a, b) for a in value_boxes for b in ref_boxes):
                return {**result, 'label_moves': references + values}
    return result


def label_move_hints(state, issues, intersects, refs=None):
    """Suggest nearby clear locations for existing reference and value collisions."""

    congested = {label_id for issue in issues if issue['kind'] in ('label_overlap', 'wire_overlaps_label')
                 for label_id in issue.get('label_ids', [issue.get('label_id')]) if label_id}
    parts = state['components']
    labels = {label['id']: label['bounds'] for part in parts for label in part.get('labels', ())
              if label.get('id') and label.get('bounds') and label.get('text')}
    fixed = [part['body_bounds'] for part in parts]
    fixed += [note['bounds'] for note in state.get('texts', ()) if note.get('bounds')]
    segments = [segment for wire in state['wires'] for segment in pairwise(wire['points'])]
    page = state['page']['bounds']
    offsets = sorted(((dx, dy) for dx in range(-36, 37, 6) for dy in range(-36, 37, 6) if dx or dy),
                     key=lambda offset: sum(n * n for n in offset))
    hints = []
    for part in parts:
        if refs is not None and part['ref'] not in refs:
            continue
        for label in part.get('labels', ()):
            label_id = label.get('id')
            if label_id not in congested or label_id not in labels or label['kind'] not in ('reference', 'value'):
                continue
            old = labels[label_id]
            cx = round((old['left'] + old['right']) / 2)
            cy = round((old['top'] + old['bottom']) / 2)
            obstacles = fixed + [box for other_id, box in labels.items() if other_id != label_id]
            for dx, dy in offsets:
                box = {key: value + (dx if key in ('left', 'right') else dy) for key, value in old.items()}
                if not (page['left'] <= box['left'] <= box['right'] <= page['right'] and
                        page['top'] <= box['top'] <= box['bottom'] <= page['bottom']):
                    continue
                if any(overlaps(box, other) for other in obstacles) or any(intersects(a, b, box) for a, b in segments):
                    continue
                labels[label_id] = box
                hints.append({'component_ref': part['id'], 'ref': part['ref'], 'kind': label['kind'],
                              'x': cx + dx, 'y': cy + dy})
                break
    return hints


def wire_networks(wires):
    """Electrical components of the visible connector graph, independent of route coordinates."""
    groups = {}
    for wire in wires:
        a, b = (wire[key]['id'] for key in ('source', 'target'))
        joined = groups.get(a, {a}) | groups.get(b, {b})
        groups.update(dict.fromkeys(joined, joined))
    return groups


def terminal_groups(wires):
    groups = wire_networks(wires)
    terminals = {end['id']: (end.get('component_id') or end['ref'], end['pin']) for w in wires for end in (w['source'], w['target'])
                 if end.get('ref') and end.get('pin')}
    return {frozenset(terminals[node] for node in groups[key] if node in terminals) for key in terminals}


def wire_overlaps(wires):
    groups = wire_networks(wires)
    by_axis = defaultdict(list)
    for wire in wires:
        pts = [(p['x'], p['y']) for p in wire['points']]
        for a, b in pairwise(pts):
            if a == b or (a[0] != b[0] and a[1] != b[1]):
                continue
            axis = int(a[0] == b[0])
            by_axis[axis, a[1 - axis]].append((min(a[axis], b[axis]), max(a[axis], b[axis]), wire))

    issues = []
    for (axis, fixed), spans in by_axis.items():
        for (a, b, left), (c, d, right) in combinations(spans, 2):
            start, end = max(a, c), min(b, d)
            if left['id'] == right['id'] or start >= end:
                continue
            shared = groups[left['source']['id']] == groups[right['source']['id']]
            points = [{'x': fixed, 'y': v} if axis else {'x': v, 'y': fixed} for v in (start, end)]
            issues.append({'kind': 'shared_wire_segment' if shared else 'distinct_wires_overlap',
                           'severity': 'warning', 'wire_ids': [left['id'], right['id']], 'points': points,
                           'same_connector_network': shared,
                           'suggestion': 'simplify_branches' if shared else 'separate_routes'})
    return issues


def reuse_parallel_trunk(state, loads, group, subtree, intersections, trunk_y):
    """Replace a single-feed branch with straight drops from a retained same-net wire."""
    removed = [w for w in state['wires'] if w['source']['id'] in subtree or w['target']['id'] in subtree]
    if any(w.get('user_named') for w in removed):
        return {'reason': 'The old branch contains user-named wires'}
    removed_ids = {w['id'] for w in removed}
    retained = [w for w in state['wires'] if w['id'] not in removed_ids]
    paths = {w['id']: normalized([(p['x'],p['y']) for p in w['points']]) for w in state['wires']}
    obstacles = [p['body_bounds'] for p in state['components']]
    obstacles += [l['bounds'] for p in state['components'] for l in p.get('labels', ()) if l.get('text') and l.get('bounds')]
    obstacles += [n['bounds'] for n in state.get('texts', ()) if n.get('bounds')]
    occupied = {(p['x'],p['y']) for c in state['components'] for p in c.get('pins', ())}
    occupied.update((e['x'],e['y']) for w in retained for e in (w['source'],w['target']))
    old_bends, old_length = map(sum, zip(*(score(paths[w['id']]) for w in removed)))
    first_x = min(pin['x'] for _,pin,_ in loads)
    last_x = max(pin['x'] for _,pin,_ in loads)
    options = []
    for wire in retained:
        if wire['source']['id'] not in group or wire.get('user_named'):
            continue
        segments = list(pairwise(paths[wire['id']]))
        for index,(a,b) in enumerate(segments):
            y = a[1]
            if b[1] != y or y % 9 or (trunk_y is not None and trunk_y != y):
                continue
            if not (min(a[0],b[0]) < first_x <= last_x < max(a[0],b[0])):
                continue
            others = [(w,paths[w['id']]) for w in retained if w['id'] != wire['id']]
            others.extend((wire,path) for i,path in enumerate(segments) if i != index)
            drops = []
            for ref,pin,(_,outward) in loads:
                tap = pin['x'], y
                endpoint = pin['x'], pin['y']
                start,end = (dict(x=x,y=yy) for x,yy in (tap,endpoint))
                if tap[0] % 9 or (y-pin['y'])*outward < 18 or covered_pins([tap,endpoint],occupied)-{endpoint}:
                    break
                if any(intersections(start,end,box) for box in obstacles):
                    break
                route = {'source': {**start,'id': 'tap'}, 'target': {**end,'id': pin['id']}}
                if contacts([tap,endpoint],route,others):
                    break
                drops.append((ref,pin,start,end))
            else:
                length = sum(abs(y-pin['y']) for _,pin,_,_ in drops)
                if (0,length) < (old_bends,old_length):
                    options.append((length,wire,a,b,drops))
    if not options:
        return {'reason': 'No existing same-net horizontal span can take all the selected loads without conflicts'}
    length, wire, a, b, drops = min(options,key=lambda item:item[0])
    operations = [{'operation':'delete_object','arguments':{'object_id': w['id']}} for w in removed]
    tail = wire['id']
    for index,(ref,pin,start,end) in enumerate(sorted(drops,key=lambda item:item[2]['x'],reverse=a[0]>b[0])):
        name = f'tap{index}'
        operations.append({'operation':'split_wire','arguments':{'wire_id':tail,**start},'bind':name})
        operations.append({'operation':'connect_endpoints','arguments':{'source':{'junction_id':{'$ref':f'{name}.junction_id'}},
                           'target':{'component_ref':ref,'pin':pin['name']},'points':[start,end]}})
        tail = {'$ref':f'{name}.wire_ids.1'}
    return {'trunk_wire_id':wire['id'],'trunk_y':a[1], 'loads':[f'{ref}.{pin["name"]}' for ref,pin,_,_ in drops],
            'operations':operations,'bends_before':old_bends,'bends_after':0,
            'length_before':old_length,'length_after':length}


def plan_parallel_trunk(state, terminals, intersects, trunk_y=None, reuse_existing=False):
    """Straighten an existing chain of junctions feeding parallel, same-facing pins."""
    loads = []
    for ref, name in terminals.items():
        matches = [part for part in state['components'] if part['ref'] == ref]
        pins = [pin for part in matches for pin in part.get('pins', ()) if pin['name'] == name]
        if len(matches) != 1 or len(pins) != 1:
            raise ValueError(f'Unknown or ambiguous load pin: {ref}.{name}')
        loads.append((ref, pins[0], pin_exit(pins[0], matches[0]['body_bounds'])))
    if len(loads) < 2:
        raise ValueError('Select at least two load pins')

    wires = state['wires']
    networks = wire_networks(wires)
    load_ids = {pin['id'] for _, pin, _ in loads}
    group = networks.get(loads[0][1]['id'], set())
    if not load_ids <= group:
        return {'reason': 'Load pins are not on the same visible native network'}
    directions = {side for _, _, side in loads}
    if directions not in ({(0, -1)}, {(0, 1)}) or len({pin['x'] for _, pin, _ in loads}) != len(loads):
        return {'reason': 'Load pins must face the same vertical direction and have distinct columns'}
    (_, outward), = directions

    adjacent = defaultdict(dict)
    endpoints = {}
    for wire in wires:
        a, b = wire['source'], wire['target']
        if a['id'] not in group:
            continue
        if b['id'] in adjacent[a['id']] or a['id'] == b['id']:
            return {'reason': 'The selected native network has parallel links or a loop'}
        adjacent[a['id']][b['id']] = adjacent[b['id']][a['id']] = wire
        endpoints[a['id']], endpoints[b['id']] = a, b
    if sum(map(len, adjacent.values())) != 2*(len(group)-1) or any(len(adjacent[i]) != 1 for i in load_ids):
        return {'reason': 'The native network must be a tree and the selected pins must be its leaves'}

    # Prune other leaves to find the smallest connected part of the network containing the loads.
    degree = {node: len(edges) for node, edges in adjacent.items()}
    leaves = deque(node for node in degree if degree[node] == 1 and node not in load_ids)
    while leaves:
        node = leaves.popleft()
        degree[node] = 0
        for neighbor in adjacent[node]:
            if degree[neighbor]:
                degree[neighbor] -= 1
                if degree[neighbor] == 1 and neighbor not in load_ids:
                    leaves.append(neighbor)
    subtree = {node for node, count in degree.items() if count}
    boundary = [(node, other, wire) for node in subtree for other, wire in adjacent[node].items() if other not in subtree]
    if len(boundary) != 1:
        return {'reason': 'Select a branch with exactly one existing feed connection'}
    if any(endpoints[node].get('ref') or endpoints[node].get('component_id') for node in subtree-load_ids):
        return {'reason': 'An unselected component pin sits inside the selected branch'}
    if trunk_y is not None and trunk_y % 9:
        raise ValueError('Trunk height must be a multiple of 9 sheet units')
    if reuse_existing:
        return reuse_parallel_trunk(state, loads, group, subtree, intersects, trunk_y)
    root, feed, feeder = boundary[0]

    loads.sort(key=lambda load: load[1]['x'])
    taps = [next(iter(adjacent[pin['id']])) for _, pin, _ in loads]
    if root != taps[0]:
        loads.reverse()
        taps.reverse()
    joints = taps[:-1]
    if (taps[0] != root or taps[-1] != taps[-2] or len(set(joints)) != len(joints)
            or set(joints) | load_ids != subtree or any(b not in adjacent[a] for a, b in pairwise(joints))
            or any(endpoints[j].get('ref') or endpoints[j].get('component_id') for j in joints)):
        return {'reason': 'Existing junctions do not form an ordered trunk from the feed to the loads'}

    def xy(end):
        return end['x'], end['y']

    def points(path):
        return [dict(x=x, y=y) for x,y in path]

    feed_point = xy(endpoints[feed])
    joint_x = [pin['x'] for _, pin, _ in loads[:-1]]
    if (feed_point[0] - joint_x[0]) * (loads[-1][1]['x'] - joint_x[0]) >= 0:
        return {'reason': 'The fixed feed does not approach the first tap from outside the trunk'}
    center = round(median(endpoints[j]['y'] for j in joints)/9)*9
    ys = [trunk_y] if trunk_y is not None else [center, *(center+offset for offset in (-9, 9, -18, 18, -27, 27))]
    page = state['page']['bounds']
    all_paths = {wire['id']: normalized([xy(p) for p in wire['points']]) for wire in wires}
    blocks = [part['body_bounds'] for part in state['components']]
    blocks += [label['bounds'] for part in state['components'] for label in part.get('labels', ())
               if label.get('text') and label.get('bounds')]
    blocks += [note['bounds'] for note in state.get('texts', ()) if note.get('bounds')]
    pins = {xy(pin) for part in state['components'] for pin in part.get('pins', ())}
    pins.update(xy(end) for wire in wires for end in (wire['source'], wire['target']) if end['id'] not in joints)
    affected = {wire['id'] for node in joints for wire in adjacent[node].values()}
    unchanged = [(wire, all_paths[wire['id']]) for wire in wires if wire['id'] not in affected]
    different = {w['id'] for w, _ in unchanged if w['source']['id'] not in group}
    previous = {wire['id']: contacts(all_paths[wire['id']], wire, unchanged) for wire in wires if wire['id'] in affected}
    old_bends, old_length = map(sum, zip(*(score(all_paths[wid]) for wid in affected)))
    best = None
    for y in ys:
        if any((y-pin['y'])*outward < 18 for _, pin, _ in loads):
            continue
        locations = {node: (x,y) for node,x in zip(joints,joint_x)}
        plans = []

        def add(wire, start_id, path):
            if wire['source']['id'] != start_id:
                path = path[::-1]
            planned = {**wire, **{key: {**wire[key], **dict(zip(('x','y'), locations[wire[key]['id']]))}
                                for key in ('source','target') if wire[key]['id'] in locations}}
            plans.append((planned, normalized(path)))

        first = locations[root]
        add(feeder, feed, [feed_point, (feed_point[0], y), first])
        for a,b in pairwise(joints):
            add(adjacent[a][b], a, [locations[a],locations[b]])
        for (_, pin, _), tap in zip(loads, taps):
            add(adjacent[tap][pin['id']], tap, [locations[tap], (pin['x'],y), xy(pin)])

        for wire, path in plans:
            if len(path) < 2 or any(not (page['left'] <= x <= page['right'] and page['top'] <= yy <= page['bottom']) for x,yy in path):
                break
            if covered_pins(path, pins) - {path[0], path[-1]} or any(intersects(a,b,box) for a,b in pairwise(points(path)) for box in blocks):
                break
            others = [(other, other_path) for other,other_path in plans if other['id'] != wire['id']]
            if contacts(path, wire, others):
                break
            hits = contacts(path, wire, unchanged)
            if (any(x0 != x1 or y0 != y1 or other not in different or
                    not proper_crossing(path, all_paths[other], x0, y0)
                    for other,x0,y0,x1,y1 in hits-previous[wire['id']])
                    or len(hits) > len(previous[wire['id']])):
                break
            # The feeder can be attached to a component; keep its native pin approach.
            if wire['id'] == feeder['id'] and endpoints[feed].get('component_id'):
                old = all_paths[wire['id']]
                if wire['target']['id'] == feed:
                    old, path = old[::-1], path[::-1]
                if direction(*path[:2]) != direction(*old[:2]):
                    break
        else:
            bends, length = map(sum, zip(*(score(path) for _,path in plans)))
            rank = bends, abs(y-center), length
            if (bends, length) < (old_bends, old_length) and (best is None or rank < best[0]):
                best = rank, y, locations, plans
    if best is None:
        return {'reason': 'No shorter or less bent trunk fits without additional wire conflicts'}
    (bends,_,length), y, locations, plans = best
    return {'trunk_y': y, 'loads': [f'{ref}.{pin["name"]}' for ref,pin,_ in loads],
            'junction_moves': [{'junction_id': key, 'x': x, 'y': yy} for key,(x,yy) in locations.items()
                               if (x,yy) != xy(endpoints[key])],
            'routes': [{'wire_id': w['id'], 'points': points(path)} for w,path in plans if path != all_paths[w['id']]],
            'bends_before': old_bends, 'bends_after': bends, 'length_before': old_length, 'length_after': length}


def common_prefix(left, right):
    """Read the common geometric prefix even when its vertices are spaced differently."""
    i = j = 1
    prefix = [left[0]]
    while i < len(left) and j < len(right):
        point = prefix[-1]
        a, b = left[i], right[j]
        if direction(point, a) != direction(point, b):
            break
        _, da = score([point, a])
        _, db = score([point, b])
        prefix.append(a if da <= db else b)
        i += da <= db
        j += db <= da
    return normalized(prefix), [prefix[-1], *left[i:]], [prefix[-1], *right[j:]]


def plan_shared_trunks(state, wire_ids=None):
    """Factor identical lead geometry into shared native junctions without adding new paths."""
    fans = defaultdict(list)
    reserved = {(pin['x'], pin['y']) for part in state['components'] for pin in part.get('pins', ())}
    reserved.update((w[k]['x'], w[k]['y']) for w in state['wires'] for k in ('source', 'target'))
    networks = wire_networks(state['wires'])
    intervals = [(networks[w['source']['id']], rectangle(a,b)) for w in state['wires']
                 for a,b in pairwise([(p['x'],p['y']) for p in w['points']])]
    for wire in state['wires']:
        if wire_ids is not None and wire['id'] not in wire_ids:
            continue
        if wire.get('user_named'):
            continue
        points = normalized([(p['x'], p['y']) for p in wire['points']])
        source, target = wire['source'], wire['target']
        if len(points) < 2 or points[0] != (source['x'], source['y']) or points[-1] != (target['x'], target['y']):
            continue
        if any(a[0] != b[0] and a[1] != b[1] for a, b in pairwise(points)):
            continue
        for reverse, path, root, other in ((False, points, source, target), (True, points[::-1], target, source)):
            fans[root['id'], direction(*path[:2])].append((wire['id'], path, other, reverse))

    operations, changes, used = [], [], set()

    def factor(items, network):
        if len(items) < 2:
            return
        prefix = items[0][1]
        for _, path, _, _ in items[1:]:
            prefix = common_prefix(prefix, path)[0]
        x, y = prefix[-1]
        if len(prefix) < 2 or x % 9 or y % 9 or (x, y) in reserved:
            return
        if any(other != network and a <= x <= b and c <= y <= d for other, (a,c,b,d) in intervals):
            return
        tails = [common_prefix(prefix, path)[2] for _, path, _, _ in items]
        if any(len(tail) < 2 for tail in tails):
            return
        first_id, _, _, backwards = items[0]
        label = f'trunk{len(changes)}'
        joint = {'junction_id': {'$ref': f'{label}.junction_id'}}
        operations.append({'operation': 'split_wire', 'arguments': {'wire_id': first_id, 'x': x, 'y': y}, 'bind': label})
        next_items = [({'$ref': f'{label}.wire_ids.{0 if backwards else 1}'}, tails[0], items[0][2], backwards)]
        for i, ((wid, _, other, _), tail) in enumerate(zip(items[1:], tails[1:]), 1):
            bind = f'{label}_branch{i}'
            end = {'component_ref': other.get('component_id') or other['ref'], 'pin': other['pin']} if other.get('ref') else {'junction_id': other['id']}
            operations.append({'operation': 'delete_object', 'arguments': {'object_id': wid}})
            operations.append({'operation': 'connect_endpoints', 'arguments': {
                'source': joint, 'target': end, 'points': [{'x': a, 'y': b} for a, b in tail]}, 'bind': bind})
            next_items.append(({'$ref': f'{bind}.id'}, tail, other, False))
        before = [score(item[1]) for item in items]
        after = [score(prefix), *map(score, tails)]
        changes.append({'junction': {'x': x, 'y': y}, 'branch_count': len(items),
                        'bends_removed': sum(p[0] for p in before) - sum(p[0] for p in after),
                        'length_removed': sum(p[1] for p in before) - sum(p[1] for p in after)})
        reserved.add((x, y))
        branches = defaultdict(list)
        for item in next_items:
            branches[direction(*item[1][:2])].append(item)
        for branch in branches.values():
            factor(branch, network)

    for (root, _), items in sorted(fans.items(), key=lambda entry: len(entry[1]), reverse=True):
        candidates = [item for item in items if item[0] not in used]
        count = len(changes)
        factor(candidates, networks[root])
        if len(changes) > count:
            used.update(item[0] for item in candidates)
    return changes, operations


def pin_alignment_hints(state, refs):
    """Estimate attainable bend counts for small, single-part alignment moves."""
    page = state['page']['bounds']
    result = []
    for part in state['components']:
        if part['ref'] not in refs and part['id'] not in refs:
            continue
        incident = []
        for wire in state['wires']:
            a, b = wire['source'], wire['target']
            if b.get('component_id') == part['id']:
                a, b = b, a
                points = wire['points'][::-1]
            else:
                points = wire['points']
            if a.get('component_id') != part['id'] or b.get('component_id') == part['id'] or len(points) < 2:
                continue
            path = normalized([(p['x'], p['y']) for p in points])
            if len(path) < 2 or any(x[0] != y[0] and x[1] != y[1] for x,y in pairwise(path)):
                continue
            dx, dy = direction(*path[:2])
            far = direction(path[-1], path[-2]) if b.get('component_id') else None
            incident.append((a, b, (dx,dy), far))

        def estimate(dx, dy):
            bends = length = 0
            for a, b, lead, far in incident:
                delta = (b['x'] - a['x'] - dx, b['y'] - a['y'] - dy)
                length += abs(delta[0]) + abs(delta[1])
                axis = int(lead[0] == 0)
                if far is not None and far[axis] == 0:
                    bends += 1 if lead[axis]*delta[axis] > 0 and far[1-axis]*delta[1-axis] < 0 else 3
                elif lead[axis]*delta[axis] > 0 and (far is None or far[axis]*delta[axis] < 0):
                    bends += (int(far is not None) + 1) if delta[1-axis] else 0
                else:
                    bends += 2 if far is None else 4
            return bends, length

        baseline = estimate(0, 0)
        candidates = []
        for a, b, (hx, hy), far in incident:
            if hx and (far is None or far == (-hx, 0)):
                dx, dy = 0, b['y'] - a['y']
            elif hy and (far is None or far == (0, -hy)):
                dx, dy = b['x'] - a['x'], 0
            else:
                continue
            if not (dx or dy) or dx % 9 or dy % 9:
                continue
            if abs(dx) + abs(dy) > 72:
                continue
            box = part['body_bounds']
            moved = (box['left'] + dx, box['top'] + dy, box['right'] + dx, box['bottom'] + dy)
            l,t,r,bottom = moved
            if not (page['left'] <= l <= r <= page['right'] and page['top'] <= t <= bottom <= page['bottom']):
                continue
            if any(max(l,o['left']) < min(r,o['right']) and max(t,o['top']) < min(bottom,o['bottom'])
                   for c in state['components'] if c['id'] != part['id'] for o in [c['body_bounds']]):
                continue
            trial = estimate(dx, dy)
            if trial[0] < baseline[0]:
                candidates.append((trial, abs(dx)+abs(dy), dx,dy,a,b))
        if candidates:
            trial, _, dx, dy, a,b = min(candidates, key=lambda item: item[:4])
            result.append({'component_ref': part['id'], 'ref': part['ref'], 'pin': a.get('pin'),
                           'align_to': {'ref': b.get('ref'), 'pin': b.get('pin'), 'x': b['x'], 'y': b['y']},
                           'dx': dx, 'dy': dy, 'incident_wires': len(incident),
                           'estimated_bends_removed': baseline[0] - trial[0],
                           'estimated_length_change': trial[1] - baseline[1]})
    return result
