// The orb.
//
// Same construction as the three.js block in the supplied design file — a
// high-segment sphere in MeshPhongMaterial, purple emissive under a warm key
// light — rebuilt against the vendored copy of three so it runs offline, and
// tuned for a 56px render where the specular highlight is what sells it.

(function () {
  const canvas = document.getElementById("orb");
  if (!canvas) return;

  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // No WebGL (or three failed to load): fall back to a CSS sphere rather
  // than leaving a blank square where the brand mark should be.
  if (typeof THREE === "undefined") return fallback();

  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
  } catch (_) {
    return fallback();
  }

  const SIZE = 56;
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(SIZE, SIZE, false);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 100);
  camera.position.z = 2.9;

  const sphere = new THREE.Mesh(
    new THREE.SphereGeometry(1, 64, 64),
    new THREE.MeshPhongMaterial({
      color: 0xd946ef,
      emissive: 0x7e22ce,
      emissiveIntensity: 0.45,
      shininess: 120,
      specular: 0xffffff,
    })
  );
  scene.add(sphere);

  scene.add(new THREE.AmbientLight(0xffffff, 0.55));

  // Key light high and to the left, which is where the highlight sits in the
  // reference. A second, dimmer rim light keeps the lower right from going flat.
  const key = new THREE.PointLight(0xffffff, 1.15);
  key.position.set(-2.2, 2.6, 3.2);
  scene.add(key);

  const rim = new THREE.PointLight(0xc084fc, 0.6);
  rim.position.set(2.6, -1.8, 1.4);
  scene.add(rim);

  let speed = 1;
  let target = 1;
  let t = 0;

  function frame() {
    requestAnimationFrame(frame);
    speed += (target - speed) * 0.06;
    t += 0.016 * speed;

    sphere.rotation.y += 0.006 * speed;
    sphere.rotation.z += 0.0018 * speed;
    const breathe = 1 + Math.sin(t * 1.5) * 0.035;
    sphere.scale.setScalar(breathe);

    renderer.render(scene, camera);
  }

  if (reduced) {
    renderer.render(scene, camera);
  } else {
    frame();
  }

  // `busy` is set by app.js while a turn is in flight. The orb spinning
  // faster is the only "thinking" indicator on the page, so it has to read
  // clearly without becoming a distraction.
  window.auraOrb = {
    busy(on) {
      target = on ? 3.4 : 1;
    },
  };

  function fallback() {
    const div = document.createElement("div");
    div.className = "orb-fallback";
    canvas.replaceWith(div);
    window.auraOrb = { busy() {} };
  }
})();
