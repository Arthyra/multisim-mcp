from unittest import TestCase
from unittest.mock import patch

from multisim_unified_mcp.sheet_layout import block_proposals, compose_sheet_edits, infer_layout_blocks, plan_sheet_layout
from multisim_unified_mcp.sheet_routing import compact_corridors, routing_neighborhoods, shared_channel_options
from multisim_unified_mcp.wiring import (
    compare_placements, label_move_hints, module_placement_proposals, pin_alignment_hints, plan_component_group, plan_shared_trunks,
    plan_local_placement, plan_local_routing, plan_ordered_wiring, plan_parallel_trunk, plan_shorter_wires,
    self_intersects, terminal_groups,
    wire_body_clearance_issues, wire_overlaps,
)


def wire(ref, vertices, source_id=None, target_id=None, component_pins=True):
    points = [{'x': x, 'y': y} for x, y in vertices]
    return {'id': ref, 'points': points,
            'source': {**points[0], 'id': source_id or ref + ':s', 'component_id': (source_id or ref + ':s') if component_pins else None,
                       'ref': source_id or ref + ':s', 'pin': '1'},
            'target': {**points[-1], 'id': target_id or ref + ':t', 'component_id': (target_id or ref + ':t') if component_pins else None,
                       'ref': target_id or ref + ':t', 'pin': '1'}}


def plan(*wires, selected=None, intersects=lambda *_: False, extra_routes=None):
    state = {'wires': wires, 'components': [], 'page': {'bounds': {'left': 0, 'top': 0, 'right': 100, 'bottom': 100}}}
    return plan_shorter_wires(state, intersects, selected, extra_routes)


class WiringTest(TestCase):
    def setUp(self):
        self.detour = wire('w', [(10, 20), (20, 20), (20, 30), (40, 30), (40, 20), (70, 20)])

    def test_straightens_a_detour_and_keeps_existing_crossing(self):
        other = wire('crossing', [(50, 10), (50, 30)])
        result, = plan(self.detour, other)
        self.assertEqual(result['points'], [{'x': 10, 'y': 20}, {'x': 70, 'y': 20}])
        self.assertEqual((result['bends_before'], result['bends_after']), (4, 0))

    def test_rejects_new_crossing_or_wire_overlap(self):
        branch = wire('other', [(30, 10), (30, 24)])
        overlap = wire('other', [(28, 20), (32, 20)])
        self.assertEqual(plan(self.detour, branch), [])
        self.assertEqual(plan(self.detour, overlap), [])

    def test_allows_ends_that_are_already_the_same_junction(self):
        branch = wire('other', [(70, 20), (70, 35)], source_id=self.detour['target']['id'])
        self.assertEqual(len(plan(self.detour, branch, selected={'w'})), 1)

    def test_preserves_pin_exit_direction(self):
        existing = wire('entry', [(10, 20), (20, 20), (20, 40), (70, 40)])
        self.assertEqual(plan(existing), [])

    def test_keeps_only_the_requested_wires(self):
        self.assertEqual(plan(self.detour, selected={'elsewhere'}), [])

    def test_removes_distinct_overlap_even_if_separation_adds_bends(self):
        straight = wire('w', [(10, 20), (70, 20)])
        separate = wire('other', [(30, 20), (50, 20)])
        detour = [(10, 20), (20, 20), (20, 40), (60, 40), (60, 20), (70, 20)]
        result, = plan(straight, separate, selected={'w'}, extra_routes={'w': [detour]})
        self.assertEqual((result['distinct_overlap_before'], result['distinct_overlap_after']), (20, 0))
        self.assertEqual((result['bends_before'], result['bends_after']), (0, 4))

    def test_prefers_fewer_crossings_over_fewer_bends(self):
        straight = wire('w', [(10, 20), (70, 20)])
        cross = wire('other', [(40, 10), (40, 30)])
        detour = [(10, 20), (20, 20), (20, 40), (60, 40), (60, 20), (70, 20)]
        result, = plan(straight, cross, selected={'w'}, extra_routes={'w': [detour]})
        self.assertEqual((result['point_contacts_before'], result['point_contacts_after']), (1, 0))

    def test_does_not_route_over_an_unconnected_pin(self):
        state = {'wires': [self.detour], 'components': [{'body_bounds': {}, 'pins': [{'x': 35, 'y': 20}]}],
                 'page': {'bounds': dict(left=0, top=0, right=100, bottom=100)}}
        self.assertEqual(plan_shorter_wires(state, lambda *_: False), [])


class OrderedWiringTest(TestCase):
    def test_plans_matched_free_pins_on_separate_columns(self):
        def component(ref, ident, x, y, left, right):
            return {'ref':ref,'id':ident,'body_bounds':dict(left=left,right=right,top=y-8,bottom=y+8),
                    'pins':[dict(id=ident+'pin',name='1',x=x,y=y)]}

        state = {'components':[component('K1','k1',18,36,0,9),component('K2','k2',18,72,0,9),
                               component('D1','d1',90,18,99,108),component('D2','d2',90,54,99,108)],
                 'wires':[], 'page':{'bounds':dict(left=0,top=0,right=117,bottom=90)}}
        pairs = [(('K1','1'),('D1','1')),(('K2','1'),('D2','1'))]
        result = plan_ordered_wiring(state,pairs,lambda *_:False,[27,36])
        self.assertEqual(result['column_order'],'ascending')
        self.assertEqual([r['source'][0] for r in result['routes']],['K1','K2'])
        self.assertEqual([r['target'][0] for r in result['routes']],['D1','D2'])
        self.assertFalse({27,36}&{r['column'] for r in result['routes']})
        self.assertEqual({op['operation'] for op in result['operations']},{'connect_endpoints'})
        self.assertEqual(len(terminal_groups(result['new_links'])),2)


class SheetLayoutTest(TestCase):
    def setUp(self):
        self.state = {'page': {'width': 162, 'height': 108,
                               'bounds': dict(left=0, top=0, right=162, bottom=108)},
                      'components': [
                          {'id': 'A', 'ref': 'A', 'body_bounds': dict(left=9, top=9, right=18, bottom=27),
                           'pins': [dict(id='A', x=27, y=18)]},
                          {'id': 'B', 'ref': 'B', 'body_bounds': dict(left=108, top=27, right=126, bottom=45),
                           'pins': [dict(id='B', x=90, y=36)]},
                          {'id': 'C', 'ref': 'C', 'body_bounds': dict(left=9, top=60, right=18, bottom=78),
                           'pins': [dict(id='C', x=27, y=69)]},
                      ],
                      'wires': [wire('a-b', [(27,18), (63,18), (63,36), (90,36)], 'A', 'B')]}

    def test_moves_a_block_together_except_for_user_locked_parts(self):
        proposals = block_proposals(self.state, {'input': ['A', 'C'], 'output': ['B']}, ['B'])
        moved = [p for p in proposals if p['moves']]
        self.assertTrue(moved)
        for plan in moved:
            self.assertEqual(plan['moves']['A'], plan['moves']['C'])
            self.assertNotIn('B', plan['moves'])

    def test_recommends_better_routing_without_editing_original_state(self):
        result, canvas = plan_sheet_layout(
            self.state, {'output': ['B']}, [], (180, 108), lambda *_: False,
            lambda a, b, _: [a, b], lambda *_: {'issues': []})
        selected = next(c for c in result['candidates'] if c['name'] == result['recommended'])
        self.assertEqual(selected['moves'], {'B': (-27, -18)})
        self.assertEqual(selected['metrics']['bends'], 0)
        self.assertEqual(canvas['arguments'], {'width': 180, 'height': 108})
        self.assertEqual(self.state['page']['width'], 162)
        self.assertEqual(self.state['wires'][0]['target']['y'], 36)

    def test_infers_a_bank_from_native_ids_and_shared_peers(self):
        a, b, c = self.state['components']
        a.update(ref='SW1')
        c.update(ref='SW2')
        a['pins'][0]['name'] = c['pins'][0]['name'] = 'OUT'
        b.update(ref='MUX')
        b['pins'][0]['name'] = 'D0'
        original = self.state['wires'][0]
        original['source'].update(ref='SW1', pin='OUT')
        original['target'].update(ref='MUX', pin='D0')
        second = wire('c-b', [(27, 69), (90, 69)], 'C', 'B')
        second['source'].update(ref='SW2', pin='OUT')
        second['target'].update(ref='MUX', pin='D1', id='B:D1')
        self.state['wires'].append(second)
        self.state['components'].append({'id': 'power', 'ref': '0', 'pins': [{'name': '1'}]})
        blocks, locked = infer_layout_blocks(self.state)
        self.assertEqual(blocks, {'SW1': ['SW1', 'SW2'], 'MUX': ['MUX']})
        self.assertEqual(locked, ['0'])

    def test_routing_window_contains_a_competing_wire_from_another_block(self):
        self.state['components'] = []
        self.state['wires'] = [
            wire('signal', [(18, 54), (108, 54)], 'IN', 'IC'),
            wire('feedback', [(72, 18), (72, 90)], 'OUT', 'RESET'),
        ]
        window, = routing_neighborhoods(self.state, {'input': ['IN', 'IC']})
        self.assertEqual(window, {'signal', 'feedback'})
        channels = shared_channel_options(self.state, window)
        for ident, options in channels.items():
            self.assertTrue(options)
            self.assertTrue(all(x0 == x1 or y0 == y1 for path in options for
                                (x0, y0), (x1, y1) in zip(path, path[1:])), ident)

    def test_compacts_empty_bus_space_without_moving_a_locked_component(self):
        self.state['wires'] = [wire('bus', [(27, 18), (135, 18)], 'A', 'B')]
        self.state['components'][1]['body_bounds'] = dict(left=144, top=9, right=153, bottom=27)
        options = compact_corridors(self.state, {'A', 'C'})
        best = options[0]
        self.assertEqual(best['moves'], {'A': (54, 0), 'C': (54, 0)})
        self.assertEqual(best['routes'][0]['points'], [dict(x=81, y=18), dict(x=135, y=18)])
        self.state['texts'] = [{'bounds': dict(left=15, top=36, right=66, bottom=45)}]
        self.assertEqual(compact_corridors(self.state, {'A', 'C'}), [])

    def test_composes_final_routes_with_moves_relative_to_the_original(self):
        first = {'moves': {'B': (9, -18)}, 'junction_moves': {'j': (9, 0)},
                 'routes': [{'wire_id': 'wire', 'points': [dict(x=9, y=18)]}]}
        final_route = {'wire_id': 'wire', 'points': [dict(x=27, y=18)]}
        second = {'moves': {'B': (18, 0), 'C': (9, 0)}, 'junction_moves': {'j': (18, 0)},
                  'routes': [final_route]}
        self.assertEqual(compose_sheet_edits(first, second), {
            'moves': {'B': (27, -18), 'C': (9, 0)},
            'junction_moves': {'j': (27, 0)},
            'routes': [final_route],
        })


class LocalRoutingTest(TestCase):
    def test_finds_compatible_pair_when_either_edited_route_alone_would_overlap(self):
        def intersects(a, b, box):
            if a['x'] == b['x']:
                return (box['left'] < a['x'] < box['right'] and
                        max(min(a['y'],b['y']),box['top']) < min(max(a['y'],b['y']),box['bottom']))
            return (box['top'] < a['y'] < box['bottom'] and
                    max(min(a['x'],b['x']),box['left']) < min(max(a['x'],b['x']),box['right']))

        a = wire('a', [(9,27),(18,27),(18,72),(27,72),(27,27),(99,27)])
        b = wire('b', [(9,63),(36,63),(36,54),(90,54),(90,63),(99,63)])
        options = {'a': [[(9,27),(36,27),(36,54),(81,54),(81,27),(99,27)]],
                   'b': [[(9,63),(18,63),(18,72),(27,72),(27,63),(99,63)]]}
        state = {'wires':[a,b], 'components':[{'id':'obstacle','body_bounds':dict(left=54,right=63,top=18,bottom=36)}],
                 'page':{'bounds':dict(left=0,top=0,right=108,bottom=90)}}
        with patch('multisim_unified_mcp.wiring.local_channel_paths', return_value=()):
            result = plan_local_routing(state,intersects,{'a','b'},options)
        best, = result['alternatives']
        self.assertEqual({edit['wire_id'] for edit in best['changes']}, {'a','b'})
        self.assertEqual(best['metrics']['distinct_overlap'], 0)
        self.assertEqual(best['metrics']['symbol_clearance_violations'], 0)
        self.assertGreater(result['baseline']['symbol_clearance_violations'], 0)

    def test_joint_preview_compares_with_fixed_route_and_leaves_locked_parts_alone(self):
        from copy import deepcopy

        a = wire('a', [(18,27),(36,27),(36,45),(81,45)], 'U1','U2')
        b = wire('b', [(18,81),(81,81)])
        state = {'wires':[a,b], 'components':[
            {'id':'U1','ref':'U1','body_bounds':dict(left=0,top=18,right=9,bottom=36)},
            {'id':'U2','ref':'U2','body_bounds':dict(left=90,top=36,right=99,bottom=54)},
        ], 'page':{'bounds':dict(left=0,top=0,right=108,bottom=99)}}
        before = deepcopy(state)

        def no_route(*_):
            raise ValueError('No extra route')

        result = plan_local_placement(state,lambda *_: False,no_route,['U2'],extra_wire_ids=['b'],
                                      candidates={'align':{'U2':(0,-18)}})
        self.assertEqual(result['recommended'],'align')
        self.assertEqual(result['fixed']['metrics']['local']['bends'],2)
        candidate, = result['candidates']
        self.assertEqual(candidate['metrics']['local']['bends'],0)
        self.assertEqual(candidate['routes'],[{'wire_id':'a','points':[{'x':18,'y':27},{'x':81,'y':27}]}])
        self.assertEqual(state,before)
        with self.assertRaisesRegex(ValueError,'explicitly unlocked'):
            plan_local_placement(state,lambda *_: False,no_route,['U2'],extra_wire_ids=['b'],
                                 candidates={'move_locked':{'U1':(9,0)}})


class GroupLayoutTest(TestCase):
    def test_preview_first_aligns_bodies_then_uses_uniform_label_offsets(self):
        def part(ref, x, y):
            return {'id': ref, 'ref': ref, 'body_bounds': dict(left=x, right=x+9, top=y, bottom=y+9),
                    'labels': [{'id': ref+'-ref', 'kind': 'reference', 'text': ref,
                                'bounds': dict(left=x, right=x+10, top=y-10, bottom=y-4)}]}

        state = {'page': {'bounds': dict(left=0, top=0, right=100, bottom=180)}, 'wires': [],
                 'components': [part('K1', 36, 18), part('K2', 36, 54), part('K3', 45, 99)]}
        refs = {'K1', 'K2', 'K3'}
        first = plan_component_group(state, refs, 'column', lambda *_: False, spacing=36)
        self.assertEqual(first['component_moves'], [{'component_ref': 'K3', 'ref': 'K3', 'dx': -9, 'dy': -9}])
        self.assertEqual(first['label_moves'], [])
        state['components'][-1] = part('K3', 36, 90)
        final = plan_component_group(state, refs, 'column', lambda *_: False, spacing=36)
        coordinates = [(item['x'], item['y']) for item in final['label_moves']]
        self.assertEqual(len(set(x for x, _ in coordinates)), 1)
        self.assertEqual([b[1]-a[1] for a,b in zip(coordinates, coordinates[1:])], [36, 36])


class PlacementComparisonTest(TestCase):
    def test_generates_distinct_module_placements_from_parallel_native_branches(self):
        def part(ref, left, top, right, bottom):
            return {'id': ref, 'ref': ref, 'body_bounds': dict(left=left, top=top, right=right, bottom=bottom)}

        parts = [part('DRIVE', 27, 99, 63, 144), part('SENSE', 198, 90, 234, 135),
                 part('TAP_B', 108, 36, 135, 54), part('TAP_A', 72, 36, 99, 54)]
        wires = []
        for index, (tap, x, y) in enumerate((('TAP_B', 117, 108), ('TAP_A', 81, 126))):
            joint = f'j{index}'
            out = wire(f'out{index}', [(72,y), (144,y)], 'DRIVE', joint)
            receive = wire(f'in{index}', [(144,y), (189,y)], joint, 'SENSE')
            branch = wire(f'tap{index}', [(144,y), (x,y), (x,63)], joint, tap)
            for item, end in ((out, 'target'), (receive, 'source'), (branch, 'source')):
                item[end].update(ref=None, component_id=None)
            out['source'].update(id=f'op{index}', pin=f'out{index}')
            receive['target'].update(id=f'ip{index}', pin=f'in{index}')
            wires.extend((out, receive, branch))

        state = {'components': parts, 'wires': wires}
        refs = {p['ref'] for p in parts}
        proposals, lanes = module_placement_proposals(state, refs)
        self.assertEqual([lane['tap'] for lane in lanes], ['TAP_B', 'TAP_A'])
        ordered = next(item for item in proposals if item['name'] == 'ordered_indicator_bank')
        reversed_order = next(item for item in proposals if item['name'] == 'ordered_indicator_bank_reversed')
        self.assertLess(ordered['tracks']['tap0'], ordered['tracks']['tap1'])
        self.assertGreater(reversed_order['tracks']['tap0'], reversed_order['tracks']['tap1'])
        self.assertTrue(all(joint in ordered['moves'] for joint in ('j0', 'j1')))

        extra = wire('extra', [(144,108),(252,108)], 'j0', 'OUTSIDE')
        extra['source'].update(ref=None, component_id=None)
        state['wires'].append(extra)
        self.assertEqual(module_placement_proposals(state, refs), ([], []))

    def test_uses_symbol_pin_side_when_old_route_approaches_from_the_wrong_side(self):
        state = {'page': {'bounds': dict(left=0, top=0, right=108, bottom=108)}, 'components': [
            {'id': 'U1', 'ref': 'U1', 'body_bounds': dict(left=0, top=9, right=9, bottom=27)},
            {'id': 'U2', 'ref': 'U2', 'body_bounds': dict(left=81, top=27, right=99, bottom=45)},
        ], 'wires': [wire('out', [(18,18),(72,18),(72,36),(63,36)], 'U1', 'U2')]}
        result = compare_placements(state, [{'name': 'aligned', 'moves': {'U2': (0, -18)}}],
                                    lambda *_: False, lambda start, end, _: [start, end])
        self.assertEqual(result['baseline']['wrong_pin_approaches'], 1)
        self.assertEqual(result['candidates'][0]['metrics']['wrong_pin_approaches'], 0)
        self.assertEqual(result['candidates'][0]['routes'][0]['points'], [{'x': 18, 'y': 18}, {'x': 63, 'y': 18}])

    def test_moves_a_junction_and_keeps_both_incident_routes_attached(self):
        left = wire('left', [(9, 18), (45, 18)], target_id='joint')
        right = wire('right', [(45, 18), (90, 18)], source_id='joint')
        left['target'].update(ref=None, component_id=None)
        right['source'].update(ref=None, component_id=None)
        state = {'page': {'bounds': dict(left=0, top=0, right=108, bottom=108)},
                 'components': [], 'wires': [left, right]}
        result = compare_placements(state, [{'name': 'shift', 'moves': {'joint': (9, 0)}}],
                                    lambda *_: False, lambda start, end, _: [start, end])
        candidate, = result['candidates']
        self.assertEqual(candidate['junction_moves'], {'joint': (9, 0)})
        self.assertEqual(candidate['routes'][0]['points'][-1], {'x': 54, 'y': 18})
        self.assertEqual(candidate['routes'][1]['points'][0], {'x': 54, 'y': 18})

    def test_ranks_moves_against_baseline_and_rejects_body_overlap_without_mutating_state(self):
        from copy import deepcopy

        def part(ref, left, top):
            return {'id': ref, 'ref': ref, 'body_bounds': dict(left=left, top=top, right=left+9, bottom=top+9)}

        state = {'page': {'bounds': dict(left=0, top=0, right=100, bottom=100)},
                 'components': [part('U1', 9, 18), part('U2', 63, 36)],
                 'wires': [wire('out', [(18, 18), (36, 18), (36, 36), (63, 36)], 'U1', 'U2')]}
        original = deepcopy(state)
        proposals = [dict(name='aligned', moves={'U2': (0, -18)}),
                     dict(name='away', moves={'U2': (0, 18)}),
                     dict(name='collision', moves={'U2': (-54, -18)})]

        def no_extra_route(*_):
            raise ValueError('No upstream route found')

        result = compare_placements(state, proposals, lambda *_: False, no_extra_route)
        self.assertEqual(result['recommended'], 'aligned')
        self.assertEqual(result['ranked'], ['aligned', 'away'])
        self.assertEqual(result['baseline']['bends'], 2)
        self.assertEqual(result['candidates'][0]['routes'][0]['points'], [{'x': 18, 'y': 18}, {'x': 63, 'y': 18}])
        self.assertEqual(result['candidates'][2]['placement_conflicts'], ['U1 / U2'])
        self.assertEqual(state, original)

    def test_rejects_self_crossing_and_backtracking_routes(self):
        self.assertFalse(self_intersects([(9, 9), (18, 9), (18, 18), (27, 18)]))
        self.assertTrue(self_intersects([(9, 9), (18, 9), (9, 9)]))
        self.assertTrue(self_intersects([(9, 9), (18, 9), (18, 18), (9, 18), (9, 0)]))

    def test_external_route_uses_same_endpoint_and_contact_rules(self):
        old = wire('w', [(10,20),(20,20),(20,15),(35,15),(35,40),(70,40)])
        chosen = [(10,20),(50,20),(50,40),(70,40)]
        def blocked(a, b, _box):
            return a['x'] == b['x'] != 50 and a['y'] < 25 < b['y']

        state = {'wires': [old], 'components': [{'body_bounds': {}}],
                 'page': {'bounds': dict(left=0, top=0, right=100, bottom=100)}}
        wrong = [(10,20),(50,20),(50,40),(69,40)]
        self.assertEqual(plan_shorter_wires(state, blocked, extra_routes={'w': [wrong]}), [])
        result, = plan_shorter_wires(state, blocked, extra_routes={'w': [chosen]})
        self.assertEqual(result['points'], [{'x': x, 'y': y} for x,y in chosen])

    def test_rejects_hugging_its_own_symbol_and_clears_the_preset_lead(self):
        def intersects(a, b, box):
            if a['x'] == b['x']:
                return (box['left'] < a['x'] < box['right'] and
                        max(min(a['y'],b['y']),box['top']) < min(max(a['y'],b['y']),box['bottom']))
            return (box['top'] < a['y'] < box['bottom'] and
                    max(min(a['x'],b['x']),box['left']) < min(max(a['x'],b['x']),box['right']))

        feedback = wire('feedback', [(90,63),(108,63),(108,36),(18,36),(18,54),(36,54)])
        feedback['source']['component_id'] = feedback['target']['component_id'] = 'U1'
        component = {'id':'U1', 'ref':'U1', 'body_bounds':dict(left=45,top=36,right=81,bottom=81),
                     'pins':[{'id':'preset','x':63,'y':18}]}
        state = {'wires':[feedback], 'components':[component],
                 'page':{'bounds':dict(left=0,top=0,right=117,bottom=99)}}
        warning, = wire_body_clearance_issues([component], [feedback], intersects)
        self.assertEqual((warning['kind'],warning['component']), ('wire_symbol_clearance','U1'))
        plan, = plan_shorter_wires(state, intersects)
        self.assertLess(min(p['y'] for p in plan['points']), component['pins'][0]['y'])
        self.assertEqual((plan['clearance_violations_before'],plan['clearance_violations_after']), (1,0))
        self.assertEqual(wire_body_clearance_issues([component],[{**feedback,'points':plan['points']}],intersects), [])
        edge_pin = wire('edge_pin', [(18,54),(45,54)])
        edge_pin['target']['component_id'] = 'U1'
        self.assertEqual(wire_body_clearance_issues([component],[edge_pin],intersects), [])


class LabelHintsTest(TestCase):
    def test_suggests_clear_in_page_location_for_reported_label_only(self):
        def box(left, top, right, bottom):
            return dict(left=left, top=top, right=right, bottom=bottom)

        state = {'page': {'bounds': box(0, 0, 100, 100)}, 'wires': [], 'components': [
            {'id': 'u1', 'ref': 'U1', 'body_bounds': box(15, 25, 40, 50), 'labels': [
                {'id': 'ref', 'text': 'U1', 'kind': 'reference', 'bounds': box(20, 20, 30, 28)},
                {'id': 'value', 'text': 'IC', 'kind': 'value', 'bounds': box(20, 10, 30, 18)},
            ]},
        ]}
        hint, = label_move_hints(state, [{'kind': 'label_overlap', 'label_id': 'ref'}], lambda *_: False)
        self.assertEqual((hint['kind'], hint['x'], hint['y']), ('reference', 13, 18))


class ParallelTrunkTest(TestCase):
    def setUp(self):
        parts = []
        for ref, x in (('R1', 45), ('R2', 81), ('R3', 117)):
            parts.append({'ref': ref, 'body_bounds': dict(left=x-4, right=x+4, top=90, bottom=108),
                          'pins': [dict(id=ref+'pin', name='1', x=x, y=81)]})
        wires = [wire('feed', [(9,27),(27,27)], 'feed_joint','j1',False),
                 wire('spine', [(27,27),(63,27)], 'j1','j2',False),
                 wire('a', [(27,27),(27,45),(45,45),(45,81)], 'j1','R1pin',False),
                 wire('b', [(63,27),(63,54),(81,54),(81,81)], 'j2','R2pin',False),
                 wire('c', [(63,27),(63,18),(117,18),(117,81)], 'j2','R3pin',False)]
        for w in wires:
            for end in (w['source'], w['target']):
                end['ref'] = next((part['ref'] for part in parts if end['id'] == part['ref']+'pin'), None)
        self.state = {'wires': wires, 'components': parts,
                      'page': {'bounds': dict(left=0, top=0, right=135, bottom=117)}}
        self.loads = {'R1':'1','R2':'1','R3':'1'}

    def test_rebuilds_staggered_routes_as_one_trunk_without_reconnecting_endpoints(self):
        result = plan_parallel_trunk(self.state, self.loads, lambda *_: False)
        self.assertEqual(result['trunk_y'], 27)
        self.assertEqual((result['bends_before'], result['bends_after']), (6,1))
        self.assertEqual(result['junction_moves'], [dict(junction_id='j1', x=45, y=27), dict(junction_id='j2', x=81, y=27)])
        routes = {r['wire_id']: r['points'] for r in result['routes']}
        self.assertEqual(routes['a'], [{'x':45, 'y':27}, {'x':45, 'y':81}])
        self.assertEqual(routes['c'], [{'x':81, 'y':27}, {'x':117, 'y':27}, {'x':117, 'y':81}])
        self.assertLess(result['length_after'], result['length_before'])

    def test_rejects_separate_networks_and_trunk_blocked_by_another_pin(self):
        blocker = {'id':'unrelated', 'name':'1', 'x':54, 'y':27}
        self.state['components'].append({'ref':'X', 'body_bounds':dict(left=50,right=58,top=4,bottom=18), 'pins':[blocker]})
        blocked = plan_parallel_trunk(self.state, self.loads, lambda *_: False, trunk_y=27)
        self.assertIn('reason', blocked)
        self.state['wires'].pop()
        rejected = plan_parallel_trunk(self.state, self.loads, lambda *_: False)
        self.assertIn('not on the same', rejected['reason'])

    def test_rehomes_loads_on_retained_same_net_main_and_rejects_foreign_main(self):
        main = wire('main', [(9,27),(9,9),(126,9)], 'feed_joint','upstream',False)
        for value in (main['source'],main['target']):
            value['ref'] = None
        self.state['wires'].append(main)
        result = plan_parallel_trunk(self.state, self.loads, lambda *_: False, reuse_existing=True)
        self.assertEqual(result['trunk_wire_id'], 'main')
        self.assertEqual((result['trunk_y'],result['bends_after']), (9,0))
        edits = result['operations']
        self.assertEqual({e['arguments']['object_id'] for e in edits if e['operation']=='delete_object'},
                         {'feed','spine','a','b','c'})
        splits = [e['arguments']['wire_id'] for e in edits if e['operation']=='split_wire']
        self.assertEqual(splits, ['main', {'$ref':'tap0.wire_ids.1'}, {'$ref':'tap1.wire_ids.1'}])
        main['source']['id'] = 'unrelated'
        self.assertIn('reason', plan_parallel_trunk(self.state, self.loads, lambda *_: False, reuse_existing=True))


class SharedTrunkTest(TestCase):
    def setUp(self):
        self.wires = [
            wire('a', [(9,9),(9,27),(54,27)], source_id='root'),
            wire('b', [(63,54),(9,54),(9,9)], target_id='root'),
            wire('c', [(9,9),(9,72),(81,72)], source_id='root'),
        ]

    def test_factors_nested_fanout_and_supports_reverse_wires(self):
        changes, edits = plan_shared_trunks({'components': [], 'wires': self.wires})
        self.assertEqual([c['junction'] for c in changes], [{'x': 9, 'y': 27}, {'x': 9, 'y': 54}])
        self.assertEqual(sum(c['length_removed'] for c in changes), 63)
        self.assertEqual(edits[0]['operation'], 'split_wire')
        self.assertEqual(edits[1]['operation'], 'delete_object')
        self.assertEqual(edits[2]['arguments']['target']['pin'], '1')

    def test_does_not_place_a_junction_on_an_unrelated_wire(self):
        stranger = wire('other', [(0,27),(8,27),(9,27),(9,45)])
        result = plan_shared_trunks({'components': [], 'wires': [*self.wires, stranger]})
        self.assertEqual(result, ([], []))

    def test_ignores_user_named_wires_and_off_grid_corners(self):
        named = [{**w, 'user_named': True} for w in self.wires[:2]]
        self.assertEqual(plan_shared_trunks({'components': [], 'wires': [*named, self.wires[2]]}), ([], []))
        off_grid = wire('d', [(9,9),(9,22),(81,22)], source_id='root')
        self.assertEqual(plan_shared_trunks({'components': [], 'wires': [self.wires[0], off_grid]}), ([], []))

    def test_overlap_diagnostics_use_connector_membership(self):
        separate = wire('other', [(9,15),(9,23)])
        issues = wire_overlaps([self.wires[0],self.wires[1],separate])
        self.assertEqual({i['kind'] for i in issues}, {'shared_wire_segment', 'distinct_wires_overlap'})
        self.assertEqual(len(terminal_groups([self.wires[0],self.wires[1],separate])), 2)


class AlignmentTest(TestCase):
    def test_aligns_a_part_only_if_the_other_attached_routes_allow_it(self):
        state = {
            'components': [{'id': 'part', 'ref': 'U2', 'body_bounds': {'left': 0, 'top': 9, 'right': 8, 'bottom': 27}}],
            'page': {'bounds': {'left': 0, 'top': 0, 'right': 100, 'bottom': 100}},
            'wires': [wire('logic', [(9,18),(27,18),(27,36),(63,36)], source_id='part')],
        }
        hint, = pin_alignment_hints(state, {'U2'})
        self.assertEqual((hint['dx'], hint['dy'], hint['estimated_bends_removed']), (0,18,2))
        state['wires'].append(wire('already_straight', [(9,18),(81,18)], source_id='part'))
        self.assertEqual(pin_alignment_hints(state, {'U2'}), [])
