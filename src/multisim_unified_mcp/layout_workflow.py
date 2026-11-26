"""Bind a reviewed layout plan to the native document and check the applied edit."""

from hashlib import sha256
from itertools import combinations
import json
from pathlib import Path
from uuid import uuid4

from .wiring import contacts, normalized, proper_crossing, terminal_groups, wire_body_clearance_issues, wire_networks, wire_overlaps


def layout_identity(host, state):
    document = host.call('inspect')['projectPath']
    content = json.dumps((document, state), sort_keys=True, separators=(',', ':'))
    return sha256(content.encode()).hexdigest()


def register_layout_plan(plans, identity, operations, expected):
    ident = uuid4().hex
    plans[ident] = (identity, operations, expected)
    if len(plans) > 24:
        plans.pop(next(iter(plans)))
    return ident


def placement_operations(state, candidate):
    junctions = {wire[end]['id']: wire[end] for wire in state['wires'] for end in ('source', 'target')
                 if not wire[end].get('ref') and not wire[end].get('component_id')}
    operations = [{'operation': 'move_component_preserving_wiring',
                   'arguments': {'component_ref': ref, 'dx': dx, 'dy': dy}}
                  for ref, (dx, dy) in candidate['moves'].items() if dx or dy]
    operations.extend({'operation': 'move_junction', 'arguments': {
        'junction_id': ident, 'x': junctions[ident]['x'] + dx, 'y': junctions[ident]['y'] + dy}}
        for ident, (dx, dy) in candidate.get('junction_moves', {}).items() if dx or dy)
    operations.extend({'operation': 'route_wire', 'arguments': route} for route in candidate['routes'])
    return operations


def checked_layout_issues(state, check_layout, intersects):
    wires = state['wires']
    relevant = [i for i in check_layout(state['components'], wires)['issues']
                if i['severity']=='error' or i['kind'] in {
                    'wire_crosses_component', 'component_overlap', 'wire_overlaps_label', 'label_overlap',
                }]
    relevant += wire_body_clearance_issues(state['components'], wires, intersects)
    relevant += [i for i in wire_overlaps(wires) if i['kind']=='distinct_wires_overlap']
    keys = {(i['kind'],i.get('wire'),i.get('component'),tuple(i.get('components', ())),
             i.get('other_component'), i.get('label_kind'), tuple(i.get('label_kinds', ())),
             tuple(sorted(i.get('wire_ids', ()))),tuple((p['x'],p['y']) for p in i.get('points', ()))) for i in relevant}
    networks = wire_networks(wires)
    paths = [(wire, normalized([(p['x'], p['y']) for p in wire['points']])) for wire in wires]
    for (first, a), (second, b) in combinations(paths, 2):
        if networks[first['source']['id']] == networks[second['source']['id']]:
            continue
        for _, x, y, right, bottom in contacts(a, first, [(second, b)]):
            if (x, y) == (right, bottom) and not proper_crossing(a, b, x, y):
                keys.add(('ambiguous_wire_touch', frozenset((first['id'], second['id'])), x, y))
    return keys


def apply_reviewed_layout(host, plan, check_layout, intersects, rollback_error, image_prefix=None, region=None):
    identity, operations, expected = plan
    before = host.call('circuitState')
    if layout_identity(host, before) != identity:
        raise ValueError('The active schematic changed after preview. Generate a fresh plan.')
    existing = checked_layout_issues(before, check_layout, intersects)
    images = {}
    if image_prefix:
        prefix = Path(image_prefix)
        if not prefix.is_absolute():
            raise ValueError('The image prefix must be an absolute path')
        images = {stage: str(prefix.with_name(f'{prefix.name}-{stage}.png')) for stage in ('before','after')}
        if any(Path(path).exists() for path in images.values()):
            raise FileExistsError('Choose an unused prefix for the comparison images')

    def export(stage):
        if images:
            host.export_image(images[stage],False,96,1.0,region,False,18,False,False)

    sheet_edits = [operation['arguments'] for operation in operations if operation['operation'] == 'sheetSize']
    if images and region is None and sheet_edits:
        page = before['page']['bounds']
        region = {**page, 'right': page['left'] + sheet_edits[0]['width'],
                  'bottom': page['top'] + sheet_edits[0]['height']}
    export('before')
    if not operations:
        export('after')
        return {'applied':False,'terminal_groups_verified':True,'images':images}
    edits = [*operations, {'operation':'repair_wire_geometry'}]
    result = host.edit_sequence(edits,{})
    try:
        after = host.call('circuitState')
        if terminal_groups(after['wires']) != expected:
            raise RuntimeError('Terminal groups differ from the reviewed plan')
        if sheet_edits and any(after['page'][key] != value for key, value in sheet_edits[0].items()):
            raise RuntimeError('Canvas dimensions differ from the reviewed plan')
        introduced = checked_layout_issues(after,check_layout,intersects) - existing
        if introduced:
            raise RuntimeError('New layout issues: ' + ', '.join(sorted({i[0] for i in introduced})))
        export('after')
    except Exception as exc:
        recovery = host.history('undo')
        raise rollback_error(f'Layout plan was undone: {exc}',recovery) from exc
    return {'applied':True,'terminal_groups_verified':True,'new_layout_issues':0,
            'undo_depth':result['undo_depth'],'images':images}
