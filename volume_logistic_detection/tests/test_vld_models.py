from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase


class TestVolumeDetectionModels(TransactionCase):

    def setUp(self):
        super().setUp()
        self.truck = self.env["vld.truck.box"].create({
            "name": "Box 6m",
            "length_cm": 600,
            "width_cm": 240,
            "height_cm": 240,
        })

    def test_truck_volume_computed(self):
        self.assertEqual(self.truck.volume_cm3, 600 * 240 * 240)

    def test_detector_classes_are_installed_with_distinct_colors(self):
        classes = {name: self.env.ref(f"volume_logistic_detection.item_class_{name}")
                   for name in ("box", "roll", "bag")}
        self.assertEqual({c.name for c in classes.values()}, {"box", "roll", "bag"})
        self.assertEqual(len({c.color for c in classes.values()}), 3)
        # No fixed size: items are sized from the video unless the user sets one.
        self.assertTrue(all(c._standard_dims() is None for c in classes.values()))

    def test_vehicle_presets_are_installed(self):
        names = ["truck_pickup_small", "truck_pickup_medium", "truck_pickup_double_cabin",
                 "truck_blind_van", "truck_cde_box", "truck_cdd_box", "truck_fuso_box",
                 "truck_tronton_box", "truck_container_20ft", "truck_container_40ft"]
        for name in names:
            truck = self.env.ref(f"volume_logistic_detection.{name}")
            self.assertEqual(truck._name, "vld.truck.box")
            self.assertGreater(min(truck.length_cm, truck.width_cm, truck.height_cm), 0, name)

    def test_vehicle_presets_range_from_pickup_to_container(self):
        pickup = self.env.ref("volume_logistic_detection.truck_pickup_small")
        van = self.env.ref("volume_logistic_detection.truck_blind_van")
        cdd = self.env.ref("volume_logistic_detection.truck_cdd_box")
        container = self.env.ref("volume_logistic_detection.truck_container_40ft")
        self.assertLess(pickup.volume_cm3, cdd.volume_cm3)
        self.assertLess(van.volume_cm3, cdd.volume_cm3)
        self.assertLess(cdd.volume_cm3, container.volume_cm3)

    def test_small_pickup_fills_up_while_a_container_does_not(self):
        from ..utils.packer import pack
        items = [{"id": i, "l": 50, "w": 40, "h": 30} for i in range(200)]
        pickup = self.env.ref("volume_logistic_detection.truck_pickup_small")
        container = self.env.ref("volume_logistic_detection.truck_container_20ft")
        in_pickup = sum(p["fitted"] for p in pack(
            pickup.length_cm, pickup.width_cm, pickup.height_cm, items))
        in_container = sum(p["fitted"] for p in pack(
            container.length_cm, container.width_cm, container.height_cm, items))
        self.assertLess(in_pickup, 200)           # the pickup runs out of room
        self.assertGreater(in_container, in_pickup)

    def test_truck_rejects_non_positive_dimension(self):
        with self.assertRaises(ValidationError):
            self.env["vld.truck.box"].create({
                "name": "Broken", "length_cm": 100, "width_cm": 100, "height_cm": 0,
            })

    def test_class_partial_standard_size_rejected(self):
        with self.assertRaises(ValidationError):
            self.env["vld.item.class"].create({"name": "partial", "length_cm": 10})

    def test_class_standard_size_returned(self):
        item_class = self.env["vld.item.class"].create({
            "name": "carton", "length_cm": 10, "width_cm": 20, "height_cm": 30,
        })
        self.assertEqual(item_class._standard_dims(), (10, 20, 30))

    def test_class_without_standard_size_returns_none(self):
        item_class = self.env["vld.item.class"].create({"name": "sack"})
        self.assertIsNone(item_class._standard_dims())

    def test_session_totals_count_only_fitted_items(self):
        session = self.env["vld.session"].create({
            "name": "Test", "truck_id": self.truck.id, "px_per_cm": 2,
        })
        Item = self.env["vld.session.item"]
        Item.create([
            {"session_id": session.id, "source": "class", "length_cm": 10, "width_cm": 10, "height_cm": 10, "fitted": True},
            {"session_id": session.id, "source": "bbox", "length_cm": 10, "width_cm": 5, "height_cm": 10, "fitted": True},
            {"session_id": session.id, "source": "bbox", "length_cm": 20, "width_cm": 20, "height_cm": 2, "fitted": False},
        ])
        self.assertEqual(session.item_count, 3)
        self.assertEqual(session.fitted_count, 2)
        self.assertAlmostEqual(session.used_volume_cm3, 1500.0)
        self.assertAlmostEqual(session.fill_ratio, 1500.0 / self.truck.volume_cm3)

    def test_session_rejects_zero_scale(self):
        with self.assertRaises(ValidationError):
            self.env["vld.session"].create({
                "name": "Bad", "truck_id": self.truck.id, "px_per_cm": 0,
            })
