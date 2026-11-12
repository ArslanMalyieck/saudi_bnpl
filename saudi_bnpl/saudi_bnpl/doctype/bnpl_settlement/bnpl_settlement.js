frappe.ui.form.on("BNPL Settlement", {
    refresh(frm) {
        if (frm.is_new()) return;
        if (frm.doc.journal_entry) {
            frm.dashboard.set_headline(__("Journal Entry linked. Check its actual status in ERPNext; this statement is not proof of settlement."));
        } else if (frm.doc.status === "Open" && frm.has_perm("write")) {
            frm.add_custom_button(__("Create Draft Journal Entry"), () => {
                if (frm.is_dirty()) {
                    frappe.msgprint(__("Save the statement before creating a draft."));
                    return;
                }
                frappe.confirm(__("Create one draft Journal Entry? Nothing will be submitted or reconciled. Finance must review all default fields and accounting dimensions, then submit it manually."), () => {
                    frappe.call({
                        method: "saudi_bnpl.saudi_bnpl.doctype.bnpl_settlement.bnpl_settlement.create_journal_entry",
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
        if (frm.is_new() && frm.doc.provider_account) {
            frappe.db.get_value("BNPL Provider Account", frm.doc.provider_account, "company", r => {
                if (r && r.company) frm.set_value("company", r.company);
            });
        }
    },
});
