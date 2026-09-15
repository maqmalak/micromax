#!/home/frappe/frappe-bench/env/bin/python3
import os
os.chdir('/home/frappe/frappe-bench')
import frappe
frappe.init(site='frontend', sites_path='/home/frappe/frappe-bench/sites')
frappe.connect()
frappe.lang = 'en'

# Clear existing demo data first
from micromax.micromax_export.demo.lc_proforma_demo import clear_demo_data
clear_demo_data()
frappe.db.commit()

# Create demo data
from micromax.micromax_export.demo.lc_proforma_demo import create_demo_data
result = create_demo_data()
print('SUCCESS:', result)
frappe.db.commit()
frappe.destroy()

# docker exec erpnext-develop-backend-1 bench --site frontend execute micromax.micromax_export.demo.lc_proforma_demo.create_demo_data
# docker exec erpnext-develop-backend-1 bench --site frontend execute micromax.micromax_export.demo.lc_proforma_demo.clear_demo_data

# generate
# docker exec erpnext-backend-1 bash -c 'cd /home/frappe/frappe-bench && env/bin/python /tmp/run_import_demo.py'
# wipe demo import data
# docker exec -i erpnext-backend-1 bench --site frontend console <<< "import frappe
# from micromax.micromax_import.demo.import_demo import clear_demo_data
# print(clear_demo_data())"
