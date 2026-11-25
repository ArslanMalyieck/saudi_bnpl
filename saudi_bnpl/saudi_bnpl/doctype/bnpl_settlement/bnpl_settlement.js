function recalculate_settlement(frm) {
    const gross = (frm.doc.invoices || [])
        .filter(row => row.selected)
        .reduce((total, row) => total + flt(row.provider_amount, 2), 0);
    frm.set_value("gross", flt(gross, 2));
    frm.set_value("net", flt(gross - flt(frm.doc.fee, 2) - flt(frm.doc.vat, 2), 2));
}

function can_fetch(frm) {
    return !frm.doc.journal_entry && frm.doc.status === "Open" && frm.has_perm("write")
        && frm.doc.provider_account && frm.doc.company && frm.doc.pos_profile
        && frm.doc.from_date && frm.doc.to_date;
}

frappe.ui.form.on("BNPL Settlement", {
    setup(frm) {
        frm.set_query("pos_profile", () => ({filters: {company: frm.doc.company}}));
    },
    refresh(frm) {
        frm.set_df_property("invoices", "read_only", Boolean(frm.doc.journal_entry));
        if (frm.doc.journal_entry) {
            frm.dashboard.set_headline(__("Journal Entry {0} is linked. Invoice selections and accounting details are locked; verify the JE's actual status in ERPNext.", [frm.doc.journal_entry]));
        } else if (frm.doc.late_warning) {
            frm.dashboard.set_headline(frm.doc.late_warning);
        }
        if (can_fetch(frm)) {
            frm.add_custom_button(__("Fetch Provider Invoices"), () => {
                frappe.call({
                    method: "saudi_bnpl.saudi_bnpl.doctype.bnpl_settlement.bnpl_settlement.fetch_provider_invoices",
                    type: "POST",
                    args: {
                        provider_account: frm.doc.provider_account,
                        company: frm.doc.company,
                        pos_profile: frm.doc.pos_profile,
                        from_date: frm.doc.from_date,
                        to_date: frm.doc.to_date,
                        settlement_name: frm.is_new() ? null : frm.doc.name,
                        existing_rows: JSON.stringify(frm.doc.invoices || []),
                    },
                    freeze: true,
                    freeze_message: __("Fetching unclaimed provider invoices..."),
                    callback(r) {
                        if (!r.message) return;
                        frm.clear_table("invoices");
                        (r.message.rows || []).forEach(row => frm.add_child("invoices", row));
                        frm.set_value("late_warning", r.message.warning || "");
                        recalculate_settlement(frm);
                        frm.refresh_fields(["invoices", "late_warning", "gross", "net"]);
                        frappe.show_alert({message: r.message.message, indicator: r.message.late_count ? "orange" : "blue"}, 8);
                    },
                });
            });
        }
        if (!frm.is_new() && !frm.doc.journal_entry && frm.doc.status === "Open" && frm.has_perm("write")) {
            frm.add_custom_button(__("Create Draft Journal Entry"), () => {
                if (frm.is_dirty()) {
                    frappe.msgprint(__("Save the settlement before creating a draft Journal Entry."));
                    return;
                }
                frappe.confirm(__("Create one draft Journal Entry and atomically claim the selected payment rows? Nothing will be submitted or reconciled."), () => {
                    frappe.call({
                        method: "saudi_bnpl.saudi_bnpl.doctype.bnpl_settlement.bnpl_settlement.create_journal_entry",
                        type: "POST",
                        args: {name: frm.doc.name},
                        freeze: true,
                        callback(r) {
                            if (r.message) frm.reload_doc();
                        },
                    });
                });
            });
        }
    },
    provider_account(frm) {
        if (!frm.doc.provider_account || !frm.is_new()) return;
        frappe.db.get_value("BNPL Provider Account", frm.doc.provider_account, "company", r => {
            if (r && r.company) frm.set_value("company", r.company);
        });
    },
    fee: recalculate_settlement,
    vat: recalculate_settlement,
});

frappe.ui.form.on("BNPL Settlement Invoice", {
    selected(frm) {
        recalculate_settlement(frm);
    },
});
