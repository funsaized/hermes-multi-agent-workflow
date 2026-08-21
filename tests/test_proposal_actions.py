from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import proposal_actions
from engine.engine import TaskSpec
from engine.item_vault import Item


class ApprovalWithoutRootLinkTests(unittest.TestCase):
    def test_missing_audit_link_does_not_block_fulfillment(self):
        with tempfile.TemporaryDirectory() as tmp:
            item = Item(
                Path(tmp) / "item.md",
                {"status": "awaiting_approval", "path": "build", "linked_kanban_tasks": []},
                "body",
            )
            vault, engine, store = Mock(), Mock(), Mock()
            vault.load.return_value = item
            engine.fulfillment_specs.return_value = [TaskSpec("build: item", "body", "builder")]
            engine.workspace_for.return_value = Path(tmp) / "workspace"
            config = Mock(board="board", paths={"build": object()}, workspace_root=tmp, pipeline_id="test")
            config.role_to_profile.return_value = "builder"
            store.connect.return_value = Mock()
            store.create_task.return_value = "t_build"

            with (
                patch.object(proposal_actions, "load_config", return_value=config),
                patch.object(proposal_actions, "ItemVault", return_value=vault),
                patch.object(proposal_actions, "TriageEngine", return_value=engine),
                patch.object(proposal_actions, "KanbanStore", return_value=store),
                patch.object(proposal_actions, "board_db", return_value=Path(tmp) / "board.db"),
            ):
                result = proposal_actions.action_approve("item")

            self.assertTrue(result["ok"])
            self.assertEqual(item.frontmatter["linked_kanban_tasks"], ["t_build"])
            store.comment.assert_not_called()

    def test_qualified_reference_must_match_loaded_pipeline(self):
        config = Mock(pipeline_id="alpha")
        self.assertEqual(proposal_actions.item_slug("alpha:item", config), "item")
        with self.assertRaisesRegex(ValueError, "targets pipeline 'beta'"):
            proposal_actions.item_slug("beta:item", config)


if __name__ == "__main__":
    unittest.main()
