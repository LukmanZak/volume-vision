/** @odoo-module **/

import { Component, onMounted, onWillDestroy, onWillStart, proxy } from "@odoo/owl";
import { rpc } from "@web/core/network/rpc";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

// Frames are sent downscaled: the model works at 640 px, so more only adds upload time.
const SEND_WIDTH = 640;
const JPEG_QUALITY = 0.6;
const IDLE_RETRY_MS = 40;
// Boxes are drawn every animation frame, moved along their measured velocity.
const MAX_EXTRAPOLATE_S = 0.6;
const MATCH_DISTANCE_PX = 160;
// With per-frame boxes, count and pack at most this often (in video seconds).
const TRACK_INTERVAL_S = 0.25;
const VELOCITY_SMOOTHING = 0.5;

export class VolumeDetection extends Component {
    static template = "volume_logistic_detection.VolumeDetection";

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.uid = `vld-${Math.random().toString(36).slice(2)}`;
        this.state = proxy({
            videos: [],
            trucks: [],
            videoName: "",
            truckId: null,
            pxPerCm: 3,
            defaultHeight: 20,
            sessionId: null,
            running: false,
            busy: false,
            analyzing: false,
            error: "",
            tracks: [],
            items: [],
            truck: null,
            fillRatio: 0,
            detections: 0,
        });

        this.timer = null;
        this.three = null;
        this.onResize = () => this.resizeRenderer();

        onWillStart(async () => {
            const [videos, trucks] = await Promise.all([
                rpc("/volume_detection/videos", {}),
                this.orm.searchRead("vld.truck.box", [], ["name", "length_cm", "width_cm", "height_cm"]),
            ]);
            this.state.videos = videos;
            this.state.videoName = videos[0] || "";
            this.state.trucks = trucks;
            this.state.truckId = trucks.length ? trucks[0].id : null;
        });
        onMounted(() => {
            this.initScene();
            this.prefetchAnalysis();
        });
        onWillDestroy(() => this.teardown());
    }

    /** Analyse the selected video in the background so Start has no wait. */
    prefetchAnalysis() {
        if (this.state.videoName) {
            this.getAnalysis(this.state.videoName);
        }
    }

    /** One analysis request per video, shared by the prefetch and Start. */
    getAnalysis(name) {
        this.analysisCache = this.analysisCache || {};
        if (!this.analysisCache[name]) {
            this.analysisCache[name] = rpc("/volume_detection/analyze", { video: name })
                .then((result) => {
                    const usable = result.status === "ok" && result.frames && result.frames.length;
                    if (!usable) {
                        delete this.analysisCache[name];
                    }
                    return usable ? result : null;
                })
                .catch(() => {
                    delete this.analysisCache[name];
                    return null;
                });
        }
        return this.analysisCache[name];
    }

    // Owl 3 has no useRef or this.el, so elements are found through the root id.
    get root() {
        return document.getElementById(this.uid);
    }

    get videoRef() {
        return { el: this.root?.querySelector("video") || null };
    }

    get canvasRef() {
        return { el: this.root?.querySelector("canvas.o_vld_canvas_hidden") || null };
    }

    get overlayRef() {
        return { el: this.root?.querySelector("canvas.o_vld_overlay") || null };
    }

    get sceneRef() {
        return { el: this.root?.querySelector(".o_vld_scene") || null };
    }

    // ----- derived values -------------------------------------------------

    get videoUrl() {
        return this.state.videoName ? `/volume_detection/video/${encodeURIComponent(this.state.videoName)}` : "";
    }

    get fillPercent() {
        return Math.round(this.state.fillRatio * 1000) / 10;
    }

    get fittedCount() {
        return this.state.items.filter((item) => item.fitted).length;
    }

    get overflowCount() {
        return this.state.items.length - this.fittedCount;
    }

    get usedVolumeCm3() {
        return this.state.items.filter((item) => item.fitted).reduce((sum, item) => sum + item.volume, 0);
    }

    fmt(value, digits = 1) {
        return Number(value).toFixed(digits);
    }

    // ----- controls -------------------------------------------------------

    onVideoChange(ev) {
        this.onStop();
        this.state.videoName = ev.target.value;
        this.prefetchAnalysis();
    }

    onTruckChange(ev) {
        this.state.truckId = Number(ev.target.value) || null;
        this.onReset();
    }

    onScaleChange(ev) {
        this.state.pxPerCm = Number(ev.target.value);
    }

    onHeightChange(ev) {
        this.state.defaultHeight = Number(ev.target.value);
    }

    async onStart() {
        this.setError("");
        if (!this.state.truckId) {
            return this.setError("Select a truck box first.");
        }
        if (!(this.state.pxPerCm > 0) || !(this.state.defaultHeight > 0)) {
            return this.setError("Scale and default height must be greater than zero.");
        }
        try {
            const result = await rpc("/volume_detection/session/start", {
                truck_id: this.state.truckId,
                px_per_cm: this.state.pxPerCm,
                default_height_cm: this.state.defaultHeight,
                name: `Detection ${new Date().toLocaleString()}`,
            });
            this.state.sessionId = result.session_id;
        } catch (err) {
            return this.setError(this.errorMessage(err));
        }
        this.clearResults();
        this.state.running = true;
        const video = this.videoRef.el;
        video.pause();
        video.currentTime = 0;
        // Detect every frame once on the server, so playback can show each frame's boxes in sync.
        this.analysis = await this.analyzeVideo();
        if (!this.state.running) {
            return; // stopped while the video was being analysed
        }
        video.play().catch(() => {});
        this.startLoop();
    }

    async analyzeVideo() {
        this.state.analyzing = true;
        try {
            const result = await this.getAnalysis(this.state.videoName);
            // null means no local model: fall back to sending frames one by one
            return result && this.videoRef.el.requestVideoFrameCallback ? result : null;
        } finally {
            this.state.analyzing = false;
        }
    }

    onStop() {
        this.stopLoop();
        if (this.videoRef.el) {
            this.videoRef.el.pause();
        }
        this.state.running = false;
    }

    async onReset() {
        this.onStop();
        if (this.state.sessionId) {
            try {
                await rpc("/volume_detection/session/reset", { session_id: this.state.sessionId });
            } catch (err) {
                this.setError(this.errorMessage(err));
            }
        }
        this.state.sessionId = null;
        this.clearResults();
    }

    clearResults() {
        this.state.tracks = [];
        this.state.items = [];
        this.state.fillRatio = 0;
        this.state.detections = 0;
        this.state.truck = null;
        this.renderScene([], null);
        this.boxSnapshot = null;
        this.drawBoxes([]);
    }

    /**
     * Draw detected bounding boxes over the video. Box coordinates are in the
     * video's native pixels, so they are scaled to the letterboxed display area.
     */
    drawBoxes(boxes) {
        const overlay = this.overlayRef.el;
        const video = this.videoRef.el;
        if (!overlay || !video) {
            return;
        }
        // The overlay covers the whole wrapper, so match its bitmap to its CSS size.
        const wrapW = overlay.clientWidth;
        const wrapH = overlay.clientHeight;
        if (overlay.width !== wrapW || overlay.height !== wrapH) {
            overlay.width = wrapW;
            overlay.height = wrapH;
        }
        const ctx = overlay.getContext("2d");
        ctx.clearRect(0, 0, wrapW, wrapH);
        const vw = video.videoWidth;
        const vh = video.videoHeight;
        if (!vw || !vh || !boxes.length) {
            return;
        }
        // The video element sits at (offsetLeft, offsetTop) inside the wrapper; fit the frame inside it.
        const elW = video.clientWidth;
        const elH = video.clientHeight;
        const scale = Math.min(elW / vw, elH / vh);
        const offsetX = video.offsetLeft + (elW - vw * scale) / 2;
        const offsetY = video.offsetTop + (elH - vh * scale) / 2;
        ctx.lineWidth = 2;
        ctx.font = "12px sans-serif";
        ctx.textBaseline = "top";
        for (const box of boxes) {
            const x = offsetX + (box.cx - box.w / 2) * scale;
            const y = offsetY + (box.cy - box.h / 2) * scale;
            const w = box.w * scale;
            const h = box.h * scale;
            const label = `${box.cls} ${Math.round(box.confidence * 100)}%`;
            const labelW = ctx.measureText(label).width + 8;
            const labelY = Math.max(0, y - 16);
            ctx.strokeStyle = "#00e676";
            ctx.strokeRect(x, y, w, h);
            ctx.fillStyle = "#00e676";
            ctx.fillRect(x, labelY, labelW, 16);
            ctx.fillStyle = "#000";
            ctx.fillText(label, x + 4, labelY + 2);
        }
    }

    // ----- frame loop -----------------------------------------------------

    startLoop() {
        this.stopLoop();
        this.looping = true;
        this.lastSentTime = -1;
        if (this.analysis) {
            this.startSyncLoop();
            return;
        }
        this.tick();
        this.drawLoop();
    }

    /**
     * Draw the precomputed boxes of the exact frame being shown. requestVideoFrameCallback
     * fires once per presented frame, so the boxes move with the video, frame by frame.
     */
    startSyncLoop() {
        const video = this.videoRef.el;
        const { frames, fps } = this.analysis;
        // Counting happens at fixed points on the video's own clock (every TRACK_INTERVAL_S),
        // each using the boxes of exactly that frame and processed in order. So the result
        // does not depend on how fast the browser or the server happens to be.
        const run = (this.trackRun = {});
        this.trackQueue = [];
        this.nextTrackTime = 0;
        const enqueueUpTo = (mediaTime) => {
            while (this.nextTrackTime <= mediaTime) {
                const index = Math.min(frames.length - 1, Math.round(this.nextTrackTime * fps));
                this.trackQueue.push({ boxes: frames[index], time: this.nextTrackTime });
                this.nextTrackTime += TRACK_INTERVAL_S;
            }
            this.drainTrackQueue(run);
        };
        this.onVideoEnded = () => {
            enqueueUpTo(video.duration);
            this.onStop();
        };
        video.addEventListener("ended", this.onVideoEnded);
        const onFrame = (now, meta) => {
            if (!this.looping) {
                return;
            }
            const index = Math.min(frames.length - 1, Math.max(0, Math.round(meta.mediaTime * fps)));
            this.drawBoxes(frames[index]);
            enqueueUpTo(meta.mediaTime);
            this.videoFrameHandle = video.requestVideoFrameCallback(onFrame);
        };
        this.videoFrameHandle = video.requestVideoFrameCallback(onFrame);
    }

    /** Send the queued counting points one after another (it keeps going after the video ends). */
    async drainTrackQueue(run) {
        if (this.draining) {
            return;
        }
        this.draining = true;
        try {
            while (this.trackQueue.length && run === this.trackRun && this.state.sessionId) {
                const { boxes, time } = this.trackQueue.shift();
                const result = await rpc("/volume_detection/track_frame", {
                    session_id: this.state.sessionId,
                    boxes,
                    tracks: this.state.tracks,
                });
                if (run !== this.trackRun) {
                    break;
                }
                if (result.status === "error") {
                    this.setError(result.message);
                    this.trackQueue = [];
                    if (result.code === "closed") {
                        this.onStop();
                    }
                    break;
                }
                this.applyResult(result, time, true);
            }
        } catch (err) {
            this.trackQueue = [];
            this.setError(this.errorMessage(err));
            this.onStop();
        } finally {
            this.draining = false;
        }
    }

    stopLoop() {
        this.looping = false;
        const video = this.videoRef.el;
        if (video && this.videoFrameHandle && video.cancelVideoFrameCallback) {
            video.cancelVideoFrameCallback(this.videoFrameHandle);
        }
        if (video && this.onVideoEnded) {
            video.removeEventListener("ended", this.onVideoEnded);
        }
        this.videoFrameHandle = null;
        this.onVideoEnded = null;
        if (this.timer) {
            clearTimeout(this.timer);
            this.timer = null;
        }
        if (this.drawRaf) {
            cancelAnimationFrame(this.drawRaf);
            this.drawRaf = null;
        }
    }

    /** Send the next frame as soon as the previous answer is in; no fixed delay. */
    async tick() {
        if (!this.looping) {
            return;
        }
        const sent = await this.sendFrame();
        if (this.looping) {
            this.timer = setTimeout(() => this.tick(), sent ? 0 : IDLE_RETRY_MS);
        }
    }

    /** Redraw the overlay every animation frame so boxes follow the playing video. */
    drawLoop() {
        if (!this.looping) {
            return;
        }
        this.drawBoxes(this.predictedBoxes());
        this.drawRaf = requestAnimationFrame(() => this.drawLoop());
    }

    /** Boxes moved from the time they were detected to the video's current time. */
    predictedBoxes() {
        const video = this.videoRef.el;
        const snap = this.boxSnapshot;
        if (!video || !snap) {
            return [];
        }
        const dt = Math.min(Math.max(video.currentTime - snap.time, 0), MAX_EXTRAPOLATE_S);
        return snap.boxes.map((box) => ({
            ...box,
            cx: box.cx + box.vx * dt,
            cy: box.cy + box.vy * dt,
            w: Math.max(2, box.w + box.vw * dt),
            h: Math.max(2, box.h + box.vh * dt),
        }));
    }

    /** Remember new detections with a velocity taken from the nearest earlier box. */
    updateBoxSnapshot(boxes, videoTime) {
        const prev = this.boxSnapshot;
        const elapsed = prev ? videoTime - prev.time : 0;
        const used = new Set();
        this.boxSnapshot = {
            time: videoTime,
            boxes: boxes.map((box) => {
                const moved = { ...box, vx: 0, vy: 0, vw: 0, vh: 0 };
                if (!prev || elapsed <= 0.01) {
                    return moved;
                }
                let best = null;
                let bestDist = MATCH_DISTANCE_PX;
                prev.boxes.forEach((old, index) => {
                    if (used.has(index) || old.cls !== box.cls) {
                        return;
                    }
                    // Compare against where the old box should be by now.
                    const dist = Math.hypot(old.cx + old.vx * elapsed - box.cx, old.cy + old.vy * elapsed - box.cy);
                    if (dist < bestDist) {
                        best = index;
                        bestDist = dist;
                    }
                });
                if (best !== null) {
                    used.add(best);
                    const old = prev.boxes[best];
                    const mix = (measured, before) => VELOCITY_SMOOTHING * measured + (1 - VELOCITY_SMOOTHING) * before;
                    moved.vx = mix((box.cx - old.cx) / elapsed, old.vx);
                    moved.vy = mix((box.cy - old.cy) / elapsed, old.vy);
                    moved.vw = mix((box.w - old.w) / elapsed, old.vw);
                    moved.vh = mix((box.h - old.h) / elapsed, old.vh);
                }
                return moved;
            }),
        };
    }

    /** Returns true when a frame was sent, false when it had to wait. */
    async sendFrame() {
        const video = this.videoRef.el;
        if (!video || video.paused || video.ended) {
            if (video && video.ended) {
                this.onStop();
            }
            return false;
        }
        if (!this.state.sessionId || video.readyState < 2 || !video.videoWidth) {
            return false;
        }
        const videoTime = video.currentTime;
        if (videoTime === this.lastSentTime) {
            return false;
        }
        this.lastSentTime = videoTime;

        const canvas = this.canvasRef.el;
        const width = Math.min(SEND_WIDTH, video.videoWidth);
        const scale = video.videoWidth / width;
        canvas.width = width;
        canvas.height = Math.round(video.videoHeight / scale);
        canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
        const imageData = canvas.toDataURL("image/jpeg", JPEG_QUALITY);

        this.state.busy = true;
        try {
            const result = await rpc("/volume_detection/detect_frame", {
                session_id: this.state.sessionId,
                image_data: imageData,
                tracks: this.state.tracks,
                scale,
            });
            if (result.status === "error") {
                this.setError(result.message);
                if (result.code === "detector_error" || result.code === "closed") {
                    this.onStop();
                }
            } else {
                this.applyResult(result, videoTime);
            }
        } catch (err) {
            this.setError(this.errorMessage(err));
            this.onStop();
        } finally {
            this.state.busy = false;
        }
        return true;
    }

    applyResult(result, videoTime, boxesAreLive = false) {
        this.state.tracks = result.tracks;
        this.state.items = result.items;
        this.state.fillRatio = result.fill_ratio;
        this.state.detections = result.detections;
        this.state.truck = result.truck;
        this.renderScene(result.items, result.truck);
        if (!boxesAreLive) {
            this.updateBoxSnapshot(result.boxes || [], videoTime);
        }
        if (result.state === "full") {
            this.trackQueue = [];
            this.notification.add("Truck box is full. Further items do not fit.", { type: "warning" });
            this.onStop();
        }
    }

    // ----- helpers --------------------------------------------------------

    setError(message) {
        this.state.error = message;
    }

    errorMessage(err) {
        return (err && err.data && err.data.message) || (err && err.message) || "Request failed.";
    }

    // ----- 3D point of view ----------------------------------------------

    initScene() {
        const THREE = window.THREE;
        if (!THREE || !THREE.OrbitControls) {
            this.setError("3D library failed to load.");
            return;
        }
        const el = this.sceneRef.el;
        const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
        renderer.setPixelRatio(window.devicePixelRatio || 1);
        el.appendChild(renderer.domElement);

        const scene = new THREE.Scene();
        const camera = new THREE.PerspectiveCamera(45, 1, 1, 100000);
        const controls = new THREE.OrbitControls(camera, renderer.domElement);
        controls.enableDamping = true;

        scene.add(new THREE.AmbientLight(0xffffff, 0.75));
        const light = new THREE.DirectionalLight(0xffffff, 0.6);
        light.position.set(1, 2, 1.5);
        scene.add(light);

        const group = new THREE.Group();
        scene.add(group);

        this.three = { THREE, renderer, scene, camera, controls, group, truckLines: null, truckKey: "", raf: null, meshes: new Map(), labels: new Map() };
        window.addEventListener("resize", this.onResize);
        this.resizeRenderer();
        this.animate();
    }

    resizeRenderer() {
        if (!this.three || !this.sceneRef.el) {
            return;
        }
        const { renderer, camera } = this.three;
        const width = this.sceneRef.el.clientWidth || 480;
        const height = this.sceneRef.el.clientHeight || 320;
        renderer.setSize(width, height);
        camera.aspect = width / height;
        camera.updateProjectionMatrix();
    }

    animate() {
        if (!this.three) {
            return;
        }
        this.three.controls.update();
        this.three.renderer.render(this.three.scene, this.three.camera);
        this.three.raf = requestAnimationFrame(() => this.animate());
    }

    /**
     * Scene axes: X = truck length, Y = height, Z = truck width.
     * Packer coordinates: x along length, y across width, z up. Items are
     * centred on the truck's floor centre so the camera orbits the box.
     */
    renderScene(items, truck) {
        if (!this.three) {
            return;
        }
        const { THREE, group, scene, camera, controls } = this.three;

        if (!truck) {
            this.disposeGroup(group);
            this.three.meshes.clear();
            this.setTruckOutline(null);
            // Forget the key, or the same truck would never get its outline drawn again.
            this.three.truckKey = "";
            return;
        }

        const L = truck.l, W = truck.w, H = truck.h;
        const key = `${L}x${W}x${H}`;
        if (key !== this.three.truckKey) {
            this.three.truckKey = key;
            this.setTruckOutline(truck);
            const distance = Math.max(L, W, H) * 1.7;
            controls.target.set(0, H / 2, 0);
            camera.position.set(distance * 0.8, H / 2 + distance * 0.6, distance * 0.9);
            camera.lookAt(0, H / 2, 0);
            controls.update();
        }

        this.disposeGroup(group);
        this.three.meshes.clear();
        for (const item of items) {
            if (!item.fitted) {
                continue;
            }
            const geometry = new THREE.BoxGeometry(item.l, item.h, item.w);
            const material = new THREE.MeshStandardMaterial({
                color: item.color,
                transparent: true,
                opacity: 0.9,
            });
            const mesh = new THREE.Mesh(geometry, material);
            const edges = new THREE.LineSegments(
                new THREE.EdgesGeometry(geometry),
                new THREE.LineBasicMaterial({ color: 0x111111 })
            );
            mesh.add(edges);
            mesh.position.set(
                item.x + item.l / 2 - L / 2,
                item.z + item.h / 2,
                item.y + item.w / 2 - W / 2
            );
            group.add(mesh);
            this.three.meshes.set(item.id, mesh);

            // The item's number, on its top face, matches the "#" column of the table.
            const size = Math.min(40, Math.max(8, Math.min(item.l, item.w) * 0.5));
            const label = new THREE.Sprite(new THREE.SpriteMaterial({
                map: this.labelTexture(`#${item.id}`),
                transparent: true,
            }));
            label.scale.set(size * 2, size, 1);
            label.position.set(mesh.position.x, item.z + item.h + size * 0.55, mesh.position.z);
            group.add(label);
        }
        this.applyHighlight();
    }

    /** A small cached texture with the item number, one per distinct text. */
    labelTexture(text) {
        const { THREE, labels } = this.three;
        if (!labels.has(text)) {
            const canvas = document.createElement("canvas");
            canvas.width = 128;
            canvas.height = 64;
            const ctx = canvas.getContext("2d");
            ctx.fillStyle = "rgba(255, 255, 255, 0.92)";
            ctx.strokeStyle = "#111";
            ctx.lineWidth = 4;
            ctx.beginPath();
            ctx.roundRect(4, 4, 120, 56, 14);
            ctx.fill();
            ctx.stroke();
            ctx.fillStyle = "#111";
            ctx.font = "bold 38px sans-serif";
            ctx.textAlign = "center";
            ctx.textBaseline = "middle";
            ctx.fillText(text, 64, 34);
            labels.set(text, new THREE.CanvasTexture(canvas));
        }
        return labels.get(text);
    }

    /** Light up one item in the 3D view (null clears it); used by the table rows. */
    highlightItem(id) {
        this.highlightedId = id;
        this.applyHighlight();
    }

    applyHighlight() {
        if (!this.three) {
            return;
        }
        for (const [id, mesh] of this.three.meshes) {
            mesh.material.emissive.setHex(id === this.highlightedId ? 0x666666 : 0x000000);
        }
    }

    setTruckOutline(truck) {
        const { THREE, scene } = this.three;
        if (this.three.truckLines) {
            scene.remove(this.three.truckLines);
            this.three.truckLines.geometry.dispose();
            this.three.truckLines.material.dispose();
            this.three.truckLines = null;
        }
        if (!truck) {
            return;
        }
        const box = new THREE.BoxGeometry(truck.l, truck.h, truck.w);
        const lines = new THREE.LineSegments(
            new THREE.EdgesGeometry(box),
            new THREE.LineBasicMaterial({ color: 0x3b4a5a })
        );
        lines.position.set(0, truck.h / 2, 0);
        box.dispose();
        scene.add(lines);
        this.three.truckLines = lines;
    }

    disposeGroup(group) {
        for (const child of [...group.children]) {
            group.remove(child);
            child.traverse((node) => {
                // Sprites share one geometry; only their own material is released.
                if (node.geometry && !node.isSprite) node.geometry.dispose();
                if (node.material) node.material.dispose();
            });
        }
    }

    teardown() {
        this.stopLoop();
        if (this.videoRef.el) {
            this.videoRef.el.pause();
        }
        window.removeEventListener("resize", this.onResize);
        if (!this.three) {
            return;
        }
        cancelAnimationFrame(this.three.raf);
        this.disposeGroup(this.three.group);
        this.setTruckOutline(null);
        for (const texture of this.three.labels.values()) {
            texture.dispose();
        }
        this.three.controls.dispose();
        this.three.renderer.dispose();
        this.three.renderer.domElement.remove();
        this.three = null;
    }
}

registry.category("actions").add("volume_detection_client", VolumeDetection);
