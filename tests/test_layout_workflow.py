from unittest import TestCase

from multisim_unified_mcp.layout_workflow import apply_reviewed_layout, checked_layout_issues, layout_identity, register_layout_plan
from multisim_unified_mcp.wiring import terminal_groups


class RollbackError(RuntimeError):
    def __init__(self, message, recovery):
        super().__init__(message)
        self.recovery = recovery


class Host:
    def __init__(self):
        self.path = 'native.ms14'
        self.original = {'components': [], 'wires': [], 'failed_layout': False}
        self.state = self.original
        self.edits = []
        self.fail_after_apply = False
        self.undone = False

    def call(self, name):
        return {'projectPath': self.path} if name == 'inspect' else self.state

    def edit_sequence(self, operations, bindings):
        self.edits.append(operations)
        self.state = {**self.state, 'failed_layout': self.fail_after_apply}
        return {'undo_depth': 1}

    def history(self, action):
        assert action == 'undo'
        self.state = self.original
        self.undone = True
        return {'restored': True}


class LayoutWorkflowTest(TestCase):
    def setUp(self):
        self.host = Host()
        self.operations = [{'operation':'route_wire','arguments':{'wire_id':'w'}}]
        self.plans = {}
        self.plan = register_layout_plan(self.plans,layout_identity(self.host,self.host.state),self.operations,
                                         terminal_groups(self.host.state['wires']))

    def apply(self):
        def layout(*_):
            return {'issues':[{'kind':'detached_endpoint','severity':'error','wire':'w'}]
                    if self.host.state['failed_layout'] else []}
        return apply_reviewed_layout(self.host,self.plans[self.plan],layout,lambda *_:False,RollbackError)

    def test_applies_one_native_edit_stage(self):
        self.assertTrue(self.apply()['terminal_groups_verified'])
        self.assertEqual(self.host.edits,[self.operations + [{'operation':'repair_wire_geometry'}]])
        self.assertFalse(self.host.undone)

    def test_rejects_another_document_before_editing(self):
        self.host.path = 'other.ms14'
        with self.assertRaisesRegex(ValueError,'changed after preview'):
            self.apply()
        self.assertEqual(self.host.edits,[])

    def test_undoes_stage_when_native_layout_reports_a_new_issue(self):
        self.host.fail_after_apply = True
        with self.assertRaisesRegex(RollbackError,'was undone') as error:
            self.apply()
        self.assertTrue(error.exception.recovery['restored'])
        self.assertIs(self.host.state,self.host.original)
        self.assertTrue(self.host.undone)

    def test_undoes_stage_if_native_terminal_groups_do_not_match(self):
        identity,ops,_ = self.plans[self.plan]
        expected = {frozenset({('U1','1'),('U2','1')})}
        self.plans[self.plan] = (identity,ops,expected)
        with self.assertRaisesRegex(RollbackError,'Terminal groups differ'):
            self.apply()
        self.assertTrue(self.host.undone)

    def test_distinguishes_a_junction_lookalike_from_a_normal_crossing(self):
        def wire(ident, xy):
            points = [dict(x=x, y=y) for x, y in xy]
            return {'id': ident, 'points': points,
                    'source': {**points[0], 'id': ident + 'a'},
                    'target': {**points[-1], 'id': ident + 'b'}}

        across = wire('across', [(0, 9), (36, 9)])
        crossing = wire('vertical', [(18, 0), (18, 18)])
        touch = wire('vertical', [(18, 0), (18, 9)])
        state = {'components': [], 'wires': [across, crossing]}
        check = lambda drawing: checked_layout_issues(drawing, lambda *_: {'issues': []}, lambda *_: False)
        self.assertEqual(check(state), set())
        self.assertEqual({issue[0] for issue in check({**state, 'wires': [across, touch]})},
                         {'ambiguous_wire_touch'})
