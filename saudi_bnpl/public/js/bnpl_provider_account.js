frappe.ui.form.on("BNPL Provider Account", {
    refresh(frm) {
        if (frm.is_new() || !frm.has_perm("delete")) return;
        frm.add_custom_button(__("Clear Provider Account"), () => {
            frappe.confirm(
                __("This will clear all settlements for this provider. Draft Journal Entries will be deleted, submitted Journal Entries will be cancelled with their accounting reversal, payment claims will be released, and then the provider account will be deleted. Continue?"),
                () => {
                    frappe.call({
                        method: "saudi_bnpl.saudi_bnpl.doctype.bnpl_settlement.bnpl_settlement.clear_provider_account",
                        type: "POST",
                        args: {name: frm.doc.name, cancel_journal: 1},
                        freeze: true,
                        callback() {
                            frappe.show_alert({message: __("Provider account cleared."), indicator: "green"}, 5);
                            frappe.set_route("List", "BNPL Provider Account");
                        },
                    });
                }
            );
        });
    },
});
