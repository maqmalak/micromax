# MicroMax — ERPNext Import / Export Customization

Customizes standard ERPNext masters & transactions for an MicroMax Import/Export
business. Uses ERPNext standard DocTypes (Item, Supplier, Customer/Buyer, Sales
Order, Purchase Order, Warehouse, BOM, ...) and only adds **Custom Fields**.
New DocTypes are limited to processes ERPNext cannot represent:

- LC Proforma (+ LC Proforma Item)
- Export Shipment
- Import Shipment
- Import Cost Sheet
- Export Packing Details

## Install (bench)

```bash
echo micromax >> sites/apps.txt
bench --site <site> install-app micromax
bench --site <site> migrate
bench --site <site> clear-cache
```