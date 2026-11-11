{
    "name": "Volume Detection",
    "summary": "Detect item volumes from video with Roboflow and pack them into a truck box",
    "version": "20.0.1.0.0",
    "category": "Inventory/Inventory",
    "license": "LGPL-3",
    "depends": ["base_setup", "web"],
    "data": [
        "security/ir.access.csv",
        "data/vld_truck_box_data.xml",
        "data/vld_item_class_data.xml",
        "views/vld_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "volume_logistic_detection/static/lib/three.min.js",
            "volume_logistic_detection/static/lib/OrbitControls.js",
            "volume_logistic_detection/static/src/js/volume_detection.js",
            "volume_logistic_detection/static/src/xml/volume_detection.xml",
            "volume_logistic_detection/static/src/scss/volume_detection.scss",
        ],
    },
    "installable": True,
    "application": True,
}
