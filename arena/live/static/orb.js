// Aura Orb — Smooth, glowing purple/magenta 3D animated sphere.
// Clean gradient shading with NO harsh specular dots, continuous floating rotation & neon aura.

(function () {
  const canvas = document.getElementById("orb");
  if (!canvas) return;

  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  if (typeof THREE === "undefined") {
    return setupFallback();
  }

  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
  } catch (_) {
    return setupFallback();
  }

  const SIZE = 68;
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(SIZE, SIZE, false);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100);
  camera.position.z = 2.9;

  // Smooth, rich magenta/violet sphere with soft diffuse sheen (no white specular dots)
  const geometry = new THREE.SphereGeometry(1, 64, 64);
  const material = new THREE.MeshPhongMaterial({
    color: 0xd946ef,
    emissive: 0x8b5cf6,
    emissiveIntensity: 0.6,
    shininess: 30,
    specular: 0xd946ef, // Tinted specular prevents harsh white dots
  });

  const sphere = new THREE.Mesh(geometry, material);
  scene.add(sphere);

  // Soft balanced ambient & directional lighting for a smooth, uniform gradient
  const ambient = new THREE.AmbientLight(0xffffff, 0.85);
  scene.add(ambient);

  const softKey = new THREE.DirectionalLight(0xffffff, 0.6);
  softKey.position.set(-1.5, 2.0, 2.5);
  scene.add(softKey);

  const softFill = new THREE.DirectionalLight(0xc084fc, 0.4);
  softFill.position.set(1.5, -1.5, 1.5);
  scene.add(softFill);

  let speed = 1.0;
  let targetSpeed = 1.0;
  let t = 0;

  function animate() {
    requestAnimationFrame(animate);

    speed += (targetSpeed - speed) * 0.08;
    t += 0.02 * speed;

    // Smooth floating rotation
    sphere.rotation.y += 0.008 * speed;
    sphere.rotation.x = Math.sin(t * 0.6) * 0.15;
    sphere.rotation.z = Math.cos(t * 0.4) * 0.1;

    // Gentle breathing pulse
    const scale = 1.0 + Math.sin(t * 1.5) * 0.03;
    sphere.scale.setScalar(scale);

    renderer.render(scene, camera);
  }

  if (reduced) {
    renderer.render(scene, camera);
  } else {
    animate();
  }

  window.auraOrb = {
    busy(isBusy) {
      targetSpeed = isBusy ? 3.5 : 1.0;
      material.emissiveIntensity = isBusy ? 0.9 : 0.6;
    },
  };

  function setupFallback() {
    const div = document.createElement("div");
    div.className = "orb-fallback";
    canvas.replaceWith(div);
    window.auraOrb = {
      busy(isBusy) {
        if (div) {
          div.style.animationDuration = isBusy ? "1.5s" : "5s";
        }
      },
    };
  }
})();
