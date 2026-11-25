app_name = "saudi_bnpl"
app_title = "Saudi BNPL"
app_publisher = "Saudi BNPL"
app_description = "Manual, draft-only BNPL statement preparation"
app_email = "admin@example.com"
app_license = "MIT"
required_apps = ["erpnext"]

doctype_js = {
    "BNPL Provider Account": "public/js/bnpl_provider_account.js",
}

after_migrate = ["saudi_bnpl.setup.after_migrate"]
