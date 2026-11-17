import { useEffect, useState } from 'react';

export function readPath() {
  const path = window.location.pathname.replace(/\/+$/, '') || '/';
  return path;
}

export function navigate(path) {
  const next = path.startsWith('/') ? path : `/${path}`;
  const normalized = next.replace(/\/+$/, '') || '/';
  if (readPath() === normalized) return;
  window.history.pushState({}, '', normalized);
  window.dispatchEvent(new PopStateEvent('popstate'));
}

export function useRoute() {
  const [path, setPath] = useState(readPath);

  useEffect(() => {
    const sync = () => setPath(readPath());
    window.addEventListener('popstate', sync);
    return () => window.removeEventListener('popstate', sync);
  }, []);

  return [path, navigate];
}
