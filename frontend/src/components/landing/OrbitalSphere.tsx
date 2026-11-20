import { useEffect, useRef } from 'react';
import * as THREE from 'three';
import { useReducedMotion } from 'framer-motion';

/**
 * Structure Flow — Orbital Sphere, layout matched to the AnimatedWebs Halcyra demo.
 * @see https://animatedwebs.com/demos/threeui-orbital-sphere/index.html
 * @see https://threeui.com/three-js/structure-flow/orbital-sphere
 */
export function OrbitalSphere() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const reduceMotion = !!useReducedMotion();

  useEffect(() => {
    const canvas = canvasRef.current;
    const stage = stageRef.current;
    if (!canvas || !stage) return;

    THREE.ColorManagement.enabled = false;

    // Same option defaults as the public Halcyra demo
    const OPTIONS = {
      speed: 1,
      particleSize: 0.015,
      particleOpacity: 0.8,
      orbitOpacity: 0.25,
      scale: 1,
      haloOpacity: 0.2,
    };

    let seed = 20260926;
    const rand = () => {
      seed |= 0;
      seed = (seed + 0x6d2b79f5) | 0;
      let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 1000);
    const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
    renderer.outputColorSpace = THREE.LinearSRGBColorSpace;
    const pixelRatio = Math.min(window.devicePixelRatio || 1, 1.75);
    renderer.setPixelRatio(pixelRatio);
    renderer.setClearColor(0x000000, 0);

    const networkGroup = new THREE.Group();
    scene.add(networkGroup);

    const radius = 2.2;
    const particleCount = 15000;
    const positions = new Float32Array(particleCount * 3);
    const colors = new Float32Array(particleCount * 3);
    // Rhythmiq palette mapped onto the same bright/dim split as the demo
    const colorBright = new THREE.Color(0x22d889);
    const colorDim = new THREE.Color(0x0a3d48);
    let validIndex = 0;

    for (let index = 0; index < particleCount; index += 1) {
      const phi = Math.acos(-1 + (2 * index) / particleCount);
      const theta = Math.sqrt(particleCount * Math.PI) * phi;
      const x = radius * Math.cos(theta) * Math.sin(phi);
      const y = radius * Math.sin(theta) * Math.sin(phi);
      const z = radius * Math.cos(phi);
      const noise = Math.sin(x * 3.5) * Math.cos(y * 3.5) * Math.sin(z * 3.5) + Math.cos(x * 6) * 0.4;
      if (noise <= -0.1) continue;
      const distortion = 1 + noise * 0.1;
      positions[validIndex * 3] = x * distortion;
      positions[validIndex * 3 + 1] = y * distortion;
      positions[validIndex * 3 + 2] = z * distortion;
      const mixed = colorDim.clone().lerp(colorBright, noise > 0.5 ? 1 : 0.3);
      colors[validIndex * 3] = mixed.r;
      colors[validIndex * 3 + 1] = mixed.g;
      colors[validIndex * 3 + 2] = mixed.b;
      validIndex += 1;
    }

    const particleGeometry = new THREE.BufferGeometry();
    particleGeometry.setAttribute('position', new THREE.BufferAttribute(positions.slice(0, validIndex * 3), 3));
    particleGeometry.setAttribute('color', new THREE.BufferAttribute(colors.slice(0, validIndex * 3), 3));
    const sizeScale = 2 / pixelRatio;
    const particleMaterial = new THREE.PointsMaterial({
      size: OPTIONS.particleSize * sizeScale,
      vertexColors: true,
      transparent: true,
      opacity: OPTIONS.particleOpacity,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    });
    networkGroup.add(new THREE.Points(particleGeometry, particleMaterial));

    const orbitMaterial = new THREE.LineBasicMaterial({
      color: 0x22d3ee,
      transparent: true,
      opacity: OPTIONS.orbitOpacity,
      blending: THREE.AdditiveBlending,
    });

    for (let index = 0; index < 6; index += 1) {
      const points: number[] = [];
      const orbitRadius = radius * (1.08 + rand() * 0.2);
      for (let p = 0; p <= 90; p += 1) {
        const a = (p / 90) * Math.PI * 2;
        points.push(Math.cos(a) * orbitRadius, Math.sin(a) * orbitRadius, Math.sin(a * 4) * 0.1);
      }
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.Float32BufferAttribute(points, 3));
      const line = new THREE.Line(geometry, orbitMaterial);
      line.rotation.x = rand() * Math.PI * 2;
      line.rotation.y = rand() * Math.PI * 2;
      networkGroup.add(line);

      if (index % 2 !== 0) {
        const node = new THREE.Mesh(
          new THREE.SphereGeometry(0.025, 16, 16),
          new THREE.MeshBasicMaterial({ color: 0x00b86b }),
        );
        const a = rand() * Math.PI * 2;
        node.position.set(Math.cos(a) * orbitRadius, Math.sin(a) * orbitRadius, 0);
        line.add(node);
        node.add(
          new THREE.Mesh(
            new THREE.SphereGeometry(0.08, 16, 16),
            new THREE.MeshBasicMaterial({
              color: 0x22d889,
              transparent: true,
              opacity: OPTIONS.haloOpacity,
              blending: THREE.AdditiveBlending,
            }),
          ),
        );
      }
    }

    const resize = () => {
      const b = stage.getBoundingClientRect();
      const width = Math.max(1, b.width);
      const height = Math.max(1, b.height);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      renderer.setSize(width, height, false);

      // Exact Halcyra demo framing — sphere sits free on the right
      let responsiveScale = 1;
      if (width >= 1024) {
        networkGroup.position.set(2.5, 0, -2);
        responsiveScale = 1.15;
        camera.position.z = 5.5;
      } else if (width >= 600) {
        networkGroup.position.set(0, 1.0, -3);
        responsiveScale = 1;
        camera.position.z = 6.5;
      } else {
        networkGroup.position.set(0, 1.55, -3);
        responsiveScale = 0.66;
        camera.position.z = 6.5;
      }
      networkGroup.scale.setScalar(responsiveScale * OPTIONS.scale);
    };

    const advance = (frames: number) => {
      networkGroup.rotation.y += 0.0008 * OPTIONS.speed * frames;
      networkGroup.rotation.x += 0.0003 * OPTIONS.speed * frames;
      networkGroup.children.forEach((child, index) => {
        if (child.type === 'Line') {
          child.rotation.z += 0.0004 * OPTIONS.speed * frames * (index % 2 === 0 ? 1 : -1);
        }
      });
    };

    let raf = 0;
    let visible = true;
    let last = 0;

    const frame = (now: number) => {
      raf = 0;
      const frames = last ? Math.min(3, (now - last) / (1000 / 60)) : 1;
      last = now;
      advance(frames);
      renderer.render(scene, camera);
      start();
    };

    const start = () => {
      if (!reduceMotion && !raf && visible && !document.hidden) {
        raf = requestAnimationFrame(frame);
      }
    };

    const stop = () => {
      if (raf) cancelAnimationFrame(raf);
      raf = 0;
      last = 0;
    };

    const ro = new ResizeObserver(() => {
      resize();
      renderer.render(scene, camera);
    });
    ro.observe(stage);

    const io = new IntersectionObserver(([e]) => {
      visible = !!e?.isIntersecting;
      if (visible) start();
      else stop();
    });
    io.observe(stage);

    const onVis = () => (document.hidden ? stop() : start());
    document.addEventListener('visibilitychange', onVis);

    resize();
    advance(reduceMotion ? 900 : 240);
    renderer.render(scene, camera);
    start();

    return () => {
      stop();
      ro.disconnect();
      io.disconnect();
      document.removeEventListener('visibilitychange', onVis);
      particleGeometry.dispose();
      particleMaterial.dispose();
      orbitMaterial.dispose();
      renderer.dispose();
    };
  }, [reduceMotion]);

  return (
    <div ref={stageRef} className="landing-orbital" aria-hidden="true">
      <canvas ref={canvasRef} className="landing-orbital-canvas" />
    </div>
  );
}
