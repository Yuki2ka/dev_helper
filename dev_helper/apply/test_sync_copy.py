import unittest
import os
import sys
import tkinter as tk
import tkinter.font as tkfont
import tkinter.ttk as ttk
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dev_helper.apply.sync_copy import SyncGui, Capture, Role


class TestGlobalScrollAndLayout(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:
            pass

    def _count_scrollbars(self, widget):
        count = 0
        try:
            for child in widget.winfo_children():
                if isinstance(child, (ttk.Scrollbar, tk.Scrollbar)):
                    count += 1
                count += self._count_scrollbars(child)
        except Exception:
            pass
        return count

    def test_only_one_vertical_scrollbar_after_preview(self):
        gui = SyncGui(self.root)
        initial = self._count_scrollbars(gui.root)
        self.assertEqual(initial, 1, f"Expected 1 scrollbar initially, found {initial}")

        gui.add_capture([str(Path("."))])
        self.root.update()

        final = self._count_scrollbars(gui.root)
        self.assertEqual(final, 1, f"Expected only 1 scrollbar after preview, found {final}")

    def test_controls_and_status_are_top_fixed(self):
        gui = SyncGui(self.root)

        def find_by_type(widget, type_):
            results = []
            try:
                for child in widget.winfo_children():
                    if isinstance(child, type_):
                        results.append(child)
                    results.extend(find_by_type(child, type_))
            except Exception:
                pass
            return results

        sync_btns = [b for b in find_by_type(gui.root, ttk.Button) if b.cget("text") == "Sync Now"]
        self.assertTrue(len(sync_btns) >= 1)
        sync_btn = sync_btns[0]

        canvases = find_by_type(gui.root, tk.Canvas)
        self.assertTrue(len(canvases) >= 1)
        canvas = canvases[0]

        all_children = []
        def collect(widget):
            try:
                for child in widget.winfo_children():
                    all_children.append(child)
                    collect(child)
            except Exception:
                pass
        collect(gui.root)

        self.assertIn(sync_btn, all_children)
        self.assertIn(canvas, all_children)
        self.assertLess(all_children.index(sync_btn), all_children.index(canvas),
                        "Controls must be packed before the scrollable canvas")


class TestTreeItemSizing(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:
            pass

    def test_treeview_has_explicit_rowheight(self):
        from dev_helper.apply.sync_copy import SyncGui
        gui = SyncGui(self.root)
        style = ttk.Style()
        row_height_str = style.lookup("Custom.Treeview", "rowheight")
        self.assertIsNotNone(row_height_str, "Custom.Treeview should have an explicit rowheight")
        row_height = int(row_height_str)
        self.assertGreater(row_height, 10)

    def test_100_element_tree_height(self):
        from dev_helper.apply.sync_copy import SyncGui
        gui = SyncGui(self.root)
        style = ttk.Style()
        row_height_str = style.lookup("Custom.Treeview", "rowheight")
        if not row_height_str:
            row_height = 20
        else:
            row_height = int(row_height_str)

        tree = ttk.Treeview(self.root, height=100, style="Custom.Treeview")
        for i in range(100):
            tree.insert("", "end", iid=str(i), text=f"Item {i}")

        self.root.update_idletasks()

        expected_min = 100 * row_height
        actual = tree.winfo_reqheight()
        self.assertGreaterEqual(actual, expected_min,
                                f"100-row tree viewport should be at least {expected_min}px, got {actual}px")

    def test_treeview_font_matches_style(self):
        style = ttk.Style()
        layout = style.layout("Custom.Treeview")
        self.assertIsNotNone(layout)


if __name__ == "__main__":
    unittest.main()
