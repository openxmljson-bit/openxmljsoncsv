//! oxj-gui — the alternative pure-Rust front-end (SPEC §11.2).
//!
//! Renders the same virtualized tree with `egui::ScrollArea::show_rows`,
//! which builds widgets only for the visible row range. Supports open,
//! expand/collapse, and regex search with the same scopes and inline
//! highlighting. Exists so the engine can ship as a single native binary
//! with no Python runtime.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use eframe::egui;
use oxj_core::{
    container_for, display_text, is_expandable, parse_document, search_nodes, Format, Index,
    Mapping, SearchScope, NIL,
};
use regex::bytes::Regex;
use std::collections::HashSet;
use std::path::PathBuf;
use std::sync::Arc;

fn main() -> eframe::Result<()> {
    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default().with_inner_size([1000.0, 700.0]),
        ..Default::default()
    };
    eframe::run_native(
        "OPENXMLJSON",
        options,
        Box::new(|_cc| Box::new(App::default())),
    )
}

struct Doc {
    mapping: Arc<Mapping>,
    index: Arc<Index>,
    format: Format,
    #[allow(dead_code)]
    path: PathBuf,
}

#[derive(Default)]
struct App {
    doc: Option<Doc>,
    expanded: HashSet<u32>,
    query: String,
    scope: SearchScope2,
    matches: HashSet<u32>,
    status: String,
}

/// Local wrapper so the combo box has a Default.
#[derive(Clone, Copy, PartialEq, Eq)]
struct SearchScope2(SearchScope);

impl Default for SearchScope2 {
    fn default() -> Self {
        SearchScope2(SearchScope::All)
    }
}

const SCOPES: [(SearchScope, &str); 4] = [
    (SearchScope::All, "All"),
    (SearchScope::Keys, "Keys"),
    (SearchScope::Values, "Values"),
    (SearchScope::Attributes, "Attributes"),
];

impl App {
    fn open_path(&mut self, path: PathBuf) {
        match Mapping::open(&path) {
            Ok(mapping) => {
                let format = Format::from_path(&path);
                match parse_document(mapping.bytes(), format) {
                    Ok(index) => {
                        let mapping = Arc::new(mapping);
                        let index = Arc::new(index);
                        self.status = format!(
                            "{} · {:.1} MB file · {} nodes · {:.1} MB index",
                            format.name(),
                            mapping.len() as f64 / 1e6,
                            group_thousands(index.len()),
                            index.byte_size() as f64 / 1e6,
                        );
                        self.expanded.clear();
                        self.matches.clear();
                        self.doc = Some(Doc {
                            mapping,
                            index,
                            format,
                            path,
                        });
                    }
                    Err(e) => self.status = format!("Parse error: {e}"),
                }
            }
            Err(e) => self.status = format!("Open error: {e}"),
        }
    }

    fn run_search(&mut self) {
        let Some(doc) = &self.doc else { return };
        if self.query.is_empty() {
            self.matches.clear();
            self.status = "Empty pattern".into();
            return;
        }
        match Regex::new(&self.query) {
            Ok(re) => {
                let ids = search_nodes(doc.mapping.bytes(), &re, self.scope.0, &doc.index);
                self.status = format!("{} matches", group_thousands(ids.len()));
                self.matches = ids.into_iter().collect();
            }
            Err(e) => self.status = format!("Invalid regex: {e}"),
        }
    }

    /// Walk visible rows (same discipline as oxj_core::TreeModel::walk);
    /// `f` returns false to stop early.
    fn walk_visible(&self, mut f: impl FnMut(u32, u32) -> bool) {
        let Some(doc) = &self.doc else { return };
        let index = &doc.index;
        let first = match container_for(index, index.root()) {
            Some(c) => index.node(c).first_child,
            None => NIL,
        };
        if first == NIL {
            return;
        }
        let mut stack: Vec<(u32, u32)> = vec![(first, 0)];
        while let Some((node, depth)) = stack.pop() {
            if !f(node, depth) {
                return;
            }
            let sib = index.node(node).next_sibling;
            if sib != NIL {
                stack.push((sib, depth));
            }
            if self.expanded.contains(&node) {
                if let Some(c) = container_for(index, node) {
                    let child = index.node(c).first_child;
                    if child != NIL {
                        stack.push((child, depth + 1));
                    }
                }
            }
        }
    }

    fn visible_row_count(&self) -> usize {
        let mut n = 0;
        self.walk_visible(|_, _| {
            n += 1;
            true
        });
        n
    }
}

fn group_thousands(n: usize) -> String {
    let s = n.to_string();
    let mut out = String::new();
    for (i, c) in s.chars().enumerate() {
        if i > 0 && (s.len() - i) % 3 == 0 {
            out.push(',');
        }
        out.push(c);
    }
    out
}

impl eframe::App for App {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        egui::TopBottomPanel::top("toolbar").show(ctx, |ui| {
            ui.horizontal(|ui| {
                if ui.button("Open…").clicked() {
                    if let Some(path) = rfd::FileDialog::new()
                        .add_filter(
                            "Documents",
                            &["json", "jsonl", "ndjson", "xml", "csv", "tsv", "tab"],
                        )
                        .pick_file()
                    {
                        self.open_path(path);
                    }
                }
                ui.separator();
                let resp = ui.add(
                    egui::TextEdit::singleline(&mut self.query)
                        .hint_text("regex…")
                        .desired_width(260.0),
                );
                let mut scope = self.scope;
                egui::ComboBox::from_id_source("scope")
                    .selected_text(
                        SCOPES
                            .iter()
                            .find(|(s, _)| *s == scope.0)
                            .map(|(_, n)| *n)
                            .unwrap_or("All"),
                    )
                    .show_ui(ui, |ui| {
                        for (s, name) in SCOPES {
                            ui.selectable_value(&mut scope, SearchScope2(s), name);
                        }
                    });
                self.scope = scope;
                let find = ui.button("Find").clicked()
                    || (resp.lost_focus() && ui.input(|i| i.key_pressed(egui::Key::Enter)));
                if find {
                    self.run_search();
                }
            });
        });

        egui::TopBottomPanel::bottom("status").show(ctx, |ui| {
            ui.label(&self.status);
        });

        egui::CentralPanel::default().show(ctx, |ui| {
            let Some(doc) = &self.doc else {
                ui.centered_and_justified(|ui| {
                    ui.label("Open a JSON / XML / CSV file to begin.");
                });
                return;
            };
            let bytes = doc.mapping.bytes();
            let index = Arc::clone(&doc.index);
            let format = doc.format;
            let total = self.visible_row_count();
            let row_height = ui.text_style_height(&egui::TextStyle::Monospace);
            let mut toggles: Vec<u32> = Vec::new();

            egui::ScrollArea::vertical().auto_shrink([false, false]).show_rows(
                ui,
                row_height,
                total,
                |ui, range| {
                    // Collect only the on-screen slice (N4).
                    let mut rows: Vec<(u32, u32)> = Vec::with_capacity(range.len());
                    let mut i = 0usize;
                    self.walk_visible(|node, depth| {
                        if i >= range.start {
                            rows.push((node, depth));
                        }
                        i += 1;
                        i < range.end
                    });
                    for (node, depth) in rows {
                        ui.horizontal(|ui| {
                            ui.add_space(depth as f32 * 16.0);
                            if is_expandable(&index, node) {
                                let open = self.expanded.contains(&node);
                                let arrow = if open { "\u{25BC}" } else { "\u{25B6}" };
                                if ui.small_button(arrow).clicked() {
                                    toggles.push(node);
                                }
                            } else {
                                ui.add_space(22.0);
                            }
                            let text = display_text(bytes, &index, format, node);
                            let label = egui::RichText::new(text).monospace();
                            if self.matches.contains(&node) {
                                ui.label(
                                    label.background_color(egui::Color32::from_rgb(255, 240, 120)),
                                );
                            } else {
                                ui.label(label);
                            }
                        });
                    }
                },
            );

            for node in toggles {
                if !self.expanded.remove(&node) {
                    self.expanded.insert(node);
                }
            }
        });
    }
}
