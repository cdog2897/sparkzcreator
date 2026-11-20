import { useEffect, useRef, useState } from 'react';
import {
  AlignCenter,
  AlignLeft,
  AlignRight,
  ArrowDown,
  ArrowLeft,
  ArrowUp,
  Copy,
  Download,
  Film,
  ImagePlus,
  Loader2,
  Plus,
  Trash2,
  Type,
  Upload,
} from 'lucide-react';

// Measured from the Brochi reference slides. The server owns the same numbers
// in text_slideshow.py; preview and export both render there, so this UI only
// places boxes.
const LINE_HEIGHT = 51 / 43;
const FRAME_WIDTH = 1080;
const FRAME_HEIGHT = 1920;
const DEFAULT_SLIDE_SECONDS = 2.75;
const DEFAULT_FIRST_SLIDE_SECONDS = 4;
const DEFAULT_CROSSFADE_SECONDS = 0.35;

const TEXT_DEFAULTS = {
  x: 130,
  width: 820,
  color: '#000000',
  align: 'center',
  lineHeight: LINE_HEIGHT,
  highlight: false,
  highlightColor: '#000000',
  highlightRadius: 12,
  highlightPadX: 23,
  highlightPadY: 16.5,
};

function newId() {
  if (window.crypto?.randomUUID) return window.crypto.randomUUID();
  return `id-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function textElement(overrides = {}) {
  return {
    id: newId(),
    type: 'text',
    text: '',
    y: 350,
    fontWeight: 'semibold',
    fontSize: 53,
    ...TEXT_DEFAULTS,
    ...overrides,
  };
}

function imageElement(overrides = {}) {
  return {
    id: newId(),
    type: 'image',
    assetId: '',
    name: 'Image',
    x: 62,
    y: 765,
    width: 405,
    height: 880,
    cornerRadius: 16,
    aspectLocked: true,
    ...overrides,
  };
}

function makeSlide(overrides = {}) {
  return {
    id: newId(),
    backgroundAssetId: null,
    backgroundColor: '#d5e4f5',
    backgroundColor2: '#f7e7ef',
    elements: [],
    ...overrides,
  };
}

function demoDeck() {
  return [
    makeSlide({
      backgroundColor: '#f4d5c4',
      backgroundColor2: '#d7e6f6',
      elements: [
        textElement({
          text: 'how to be a better partner',
          fontWeight: 'medium',
          fontSize: 66,
          y: 352,
        }),
        textElement({
          text: '(these tips actually helped)',
          fontWeight: 'medium',
          fontSize: 49,
          y: 442,
        }),
      ],
    }),
    makeSlide({
      backgroundColor: '#c9dff3',
      backgroundColor2: '#f8e6ef',
      elements: [
        textElement({ text: '1. give them space' }),
        textElement({
          y: 438,
          fontWeight: 'medium',
          fontSize: 43,
          text: [
            'if they do not want to talk about it yet,',
            'do not push. let them sit with it.',
            '',
            'not everything has to happen on your clock.',
            'sometimes they need a minute alone',
            'before they can explain it well.',
            '',
            'pushing harder only moves them further away.',
          ].join('\n'),
        }),
      ],
    }),
    makeSlide({
      backgroundColor: '#f6e3cf',
      backgroundColor2: '#d5e3ea',
      elements: [
        textElement({ text: '2. plan the next day' }),
        textElement({
          y: 438,
          fontWeight: 'medium',
          fontSize: 43,
          text: [
            'routine wears a relationship down faster',
            'than one bad argument. if you always wait',
            'for them to organize something, say so.',
            '',
            'make the plan together. write down the',
            'days you still want to have.',
          ].join('\n'),
        }),
      ],
    }),
    makeSlide({
      backgroundColor: '#ead7c8',
      backgroundColor2: '#f6ece4',
      elements: [
        textElement({ text: '3. keep it somewhere shared' }),
        textElement({
          y: 438,
          fontWeight: 'medium',
          fontSize: 43,
          text: [
            'a note on your phone is easy to lose.',
            'put the list where you both can open it.',
          ].join('\n'),
        }),
        textElement({
          text: 'it is called \u201csparkz\u201d',
          x: 517,
          y: 798.5,
          width: 560,
          fontWeight: 'medium',
          fontSize: 41,
          color: '#ffffff',
          align: 'left',
          highlight: true,
        }),
      ],
    }),
  ];
}

function toApiElement(element) {
  if (element.type === 'image') {
    return {
      type: 'image',
      asset_id: element.assetId,
      x: element.x,
      y: element.y,
      width: element.width,
      height: element.height,
      corner_radius: element.cornerRadius,
    };
  }
  return {
    type: 'text',
    text: element.text,
    x: element.x,
    y: element.y,
    width: element.width,
    font_weight: element.fontWeight,
    font_size: element.fontSize,
    color: element.color,
    align: element.align,
    line_height: element.lineHeight,
    highlight: element.highlight,
    highlight_color: element.highlightColor,
    highlight_radius: element.highlightRadius,
    highlight_pad_x: element.highlightPadX,
    highlight_pad_y: element.highlightPadY,
  };
}

function toApiSlide(slide) {
  return {
    background_asset_id: slide.backgroundAssetId,
    background_color: slide.backgroundColor,
    background_color_2: slide.backgroundColor2,
    elements: slide.elements.map(toApiElement),
  };
}

async function readError(response) {
  try {
    const payload = await response.json();
    return payload.detail || response.statusText;
  } catch {
    return response.statusText;
  }
}

function estimateBlockHeight(element) {
  if (element.type === 'image') return element.height;
  const lineCount = element.text.split('\n').reduce((count, line) => {
    const perLine = Math.max(8, Math.floor(element.width / (element.fontSize * 0.48)));
    return count + Math.max(1, Math.ceil(Math.max(1, line.length) / perLine));
  }, 0);
  return Math.max(element.fontSize * element.lineHeight, lineCount * element.fontSize * element.lineHeight);
}

function quantizeReelSeconds(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) return 0;
  return Math.max(1, Math.round(seconds * 30)) / 30;
}

function reelDuration(slideCount, seconds, firstSeconds, fadeSeconds) {
  if (slideCount < 1) return 0;
  const perSlide = quantizeReelSeconds(seconds);
  const first = firstSeconds == null ? perSlide : quantizeReelSeconds(firstSeconds);
  const holds = Array.from({ length: slideCount }, (_, index) => (index === 0 ? first : perSlide));
  const fade = slideCount > 1 ? quantizeReelSeconds(fadeSeconds) : 0;
  const overlap = slideCount > 1 ? (slideCount - 1) * fade : 0;
  return Math.max(0, holds.reduce((total, hold) => total + hold, 0) - overlap);
}

function slideSnippet(slide, index) {
  const text = slide.elements.find((element) => element.type === 'text' && element.text.trim());
  if (!text) return `Slide ${index + 1}`;
  return text.text.split('\n').find((line) => line.trim()) || `Slide ${index + 1}`;
}

export default function TextSlideshow({ onHome }) {
  const [slides, setSlides] = useState(demoDeck);
  const [selected, setSelected] = useState(0);
  const [selectedElementId, setSelectedElementId] = useState('');
  const [fonts, setFonts] = useState([
    { id: 'regular', name: 'Regular' },
    { id: 'medium', name: 'Medium' },
    { id: 'semibold', name: 'SemiBold' },
    { id: 'bold', name: 'Bold' },
  ]);
  const [previews, setPreviews] = useState({});
  const [error, setError] = useState('');
  const [isExporting, setIsExporting] = useState(false);
  const [isExportingVideo, setIsExportingVideo] = useState(false);
  const [slideSeconds, setSlideSeconds] = useState(DEFAULT_SLIDE_SECONDS);
  const [holdFirstSlide, setHoldFirstSlide] = useState(false);
  const [firstSlideSeconds, setFirstSlideSeconds] = useState(DEFAULT_FIRST_SLIDE_SECONDS);
  const [transition, setTransition] = useState('cut');
  const [fadeSeconds, setFadeSeconds] = useState(DEFAULT_CROSSFADE_SECONDS);
  const [audioFile, setAudioFile] = useState(null);
  const [audioPreviewUrl, setAudioPreviewUrl] = useState('');
  const [musicTracks, setMusicTracks] = useState([]);
  const [selectedMusicName, setSelectedMusicName] = useState('');
  const [musicUrl, setMusicUrl] = useState('');
  const [useChromeLogin, setUseChromeLogin] = useState(false);
  const [isImportingMusic, setIsImportingMusic] = useState(false);
  const [reelMessage, setReelMessage] = useState('');
  const frameRef = useRef(null);
  const dragRef = useRef(null);
  const backgroundInputRef = useRef(null);
  const overlayInputRef = useRef(null);
  const audioInputRef = useRef(null);

  const slide = slides[selected] || slides[0];
  const selectedElement = slide?.elements.find((element) => element.id === selectedElementId) || null;
  const selectedTrack = musicTracks.find((track) => track.name === selectedMusicName) || null;

  useEffect(() => {
    let mounted = true;
    fetch('/api/music')
      .then((response) => (response.ok ? response.json() : null))
      .then((payload) => {
        if (mounted && payload?.music) setMusicTracks(payload.music);
      })
      .catch(() => {});
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    if (!audioFile) {
      setAudioPreviewUrl('');
      return undefined;
    }
    const url = URL.createObjectURL(audioFile);
    setAudioPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [audioFile]);

  useEffect(() => {
    let mounted = true;
    fetch('/api/text-slideshow')
      .then((response) => (response.ok ? response.json() : null))
      .then((payload) => {
        if (mounted && payload?.fonts?.length) setFonts(payload.fonts);
      })
      .catch(() => {});
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    const current = slides[selected];
    if (!current) return undefined;
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      try {
        const response = await fetch('/api/text-slideshow/preview', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(toApiSlide(current)),
          signal: controller.signal,
        });
        if (!response.ok) throw new Error(await readError(response));
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        setPreviews((existing) => {
          const previous = existing[current.id];
          if (previous) URL.revokeObjectURL(previous);
          return { ...existing, [current.id]: url };
        });
        setError('');
      } catch (caught) {
        if (caught.name !== 'AbortError') setError(caught.message);
      }
    }, 90);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [slides, selected]);

  const previewsRef = useRef(previews);
  previewsRef.current = previews;
  useEffect(() => () => {
    Object.values(previewsRef.current).forEach((url) => URL.revokeObjectURL(url));
  }, []);

  useEffect(() => {
    function onMove(event) {
      const drag = dragRef.current;
      if (!drag) return;
      const dx = (event.clientX - drag.clientX) / drag.scale;
      const dy = (event.clientY - drag.clientY) / drag.scale;
      const orig = drag.element;
      if (drag.mode === 'move') {
        patchElement(drag.slideIndex, orig.id, {
          x: Math.round(orig.x + dx),
          y: Math.round(orig.y + dy),
        });
      } else if (drag.mode === 'resize-right') {
        patchElement(drag.slideIndex, orig.id, {
          width: Math.max(80, Math.round(orig.width + dx)),
        });
      } else if (drag.mode === 'resize-left') {
        const width = Math.max(80, Math.round(orig.width - dx));
        patchElement(drag.slideIndex, orig.id, {
          x: Math.round(orig.x + orig.width - width),
          width,
        });
      } else if (drag.mode === 'resize-corner') {
        const width = Math.max(40, Math.round(orig.width + dx));
        const height = orig.aspectLocked
          ? Math.max(40, Math.round(width * (orig.height / orig.width)))
          : Math.max(40, Math.round(orig.height + dy));
        patchElement(drag.slideIndex, orig.id, { width, height });
      }
    }
    function onUp() {
      dragRef.current = null;
    }
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    return () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };
  }, []);

  function patchSlide(index, patch) {
    setSlides((current) => current.map((item, itemIndex) => (
      itemIndex === index ? { ...item, ...patch } : item
    )));
  }

  function patchElement(slideIndex, elementId, patch) {
    setSlides((current) => current.map((item, itemIndex) => {
      if (itemIndex !== slideIndex) return item;
      return {
        ...item,
        elements: item.elements.map((element) => (
          element.id === elementId ? { ...element, ...patch } : element
        )),
      };
    }));
  }

  function addElement(element) {
    setSlides((current) => current.map((item, index) => (
      index === selected ? { ...item, elements: [...item.elements, element] } : item
    )));
    setSelectedElementId(element.id);
  }

  function startDrag(event, element, mode) {
    event.stopPropagation();
    event.preventDefault();
    const rect = frameRef.current?.getBoundingClientRect();
    const scale = rect?.width ? rect.width / FRAME_WIDTH : 0.3;
    dragRef.current = {
      mode,
      slideIndex: selected,
      element: { ...element },
      clientX: event.clientX,
      clientY: event.clientY,
      scale,
    };
    setSelectedElementId(element.id);
  }

  async function uploadAsset(file) {
    const body = new FormData();
    body.append('file', file, file.name);
    const response = await fetch('/api/text-slideshow/assets', { method: 'POST', body });
    if (!response.ok) throw new Error(await readError(response));
    return response.json();
  }

  async function onBackgroundFile(file) {
    if (!file) return;
    setError('');
    try {
      const asset = await uploadAsset(file);
      patchSlide(selected, { backgroundAssetId: asset.id });
    } catch (caught) {
      setError(caught.message);
    }
  }

  async function onOverlayFile(file) {
    if (!file) return;
    setError('');
    try {
      const asset = await uploadAsset(file);
      const aspect = asset.width / Math.max(1, asset.height);
      let width = 405;
      let height = Math.round(width / aspect);
      if (height > 1100) {
        height = 880;
        width = Math.round(height * aspect);
      }
      addElement(imageElement({
        assetId: asset.id,
        name: file.name,
        width,
        height,
      }));
    } catch (caught) {
      setError(caught.message);
    }
  }

  function moveSlide(index, direction) {
    const target = index + direction;
    if (target < 0 || target >= slides.length) return;
    setSlides((current) => {
      const next = current.slice();
      const [item] = next.splice(index, 1);
      next.splice(target, 0, item);
      return next;
    });
    setSelected(target);
    setSelectedElementId('');
  }

  function duplicateSlide(index) {
    const source = slides[index];
    const copy = {
      ...source,
      id: newId(),
      elements: source.elements.map((element) => ({ ...element, id: newId() })),
    };
    setSlides((current) => {
      const next = current.slice();
      next.splice(index + 1, 0, copy);
      return next;
    });
    setSelected(index + 1);
    setSelectedElementId('');
  }

  function deleteSlide(index) {
    if (slides.length === 1) return;
    setSlides((current) => current.filter((_, itemIndex) => itemIndex !== index));
    setSelected((current) => {
      if (current > index) return current - 1;
      if (current === index) return Math.max(0, index - 1);
      return current;
    });
    setSelectedElementId('');
  }

  function moveElement(elementId, direction) {
    setSlides((current) => current.map((item, index) => {
      if (index !== selected) return item;
      const from = item.elements.findIndex((element) => element.id === elementId);
      const target = from + direction;
      if (from < 0 || target < 0 || target >= item.elements.length) return item;
      const elements = item.elements.slice();
      const [element] = elements.splice(from, 1);
      elements.splice(target, 0, element);
      return { ...item, elements };
    }));
  }

  function deleteElement(elementId) {
    setSlides((current) => current.map((item, index) => (
      index === selected
        ? { ...item, elements: item.elements.filter((element) => element.id !== elementId) }
        : item
    )));
    setSelectedElementId('');
  }

  async function exportZip() {
    if (isExporting || isExportingVideo) return;
    setIsExporting(true);
    setError('');
    try {
      const response = await fetch('/api/text-slideshow/export', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ slides: slides.map(toApiSlide) }),
      });
      if (!response.ok) throw new Error(await readError(response));
      const blob = await response.blob();
      const disposition = response.headers.get('content-disposition') || '';
      const matched = disposition.match(/filename="?([^"]+)"?/);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = matched?.[1] || 'text-slideshow.zip';
      link.click();
      URL.revokeObjectURL(url);
    } catch (caught) {
      setError(caught.message);
    } finally {
      setIsExporting(false);
    }
  }

  async function fetchMusicFromUrl() {
    const url = musicUrl.trim();
    if (!url) {
      setError('Enter an Instagram video URL.');
      return;
    }
    setError('');
    setIsImportingMusic(true);
    try {
      const response = await fetch('/api/music/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          url,
          use_browser_cookies: useChromeLogin,
          browser: 'chrome',
        }),
      });
      if (!response.ok) throw new Error(await readError(response));
      const payload = await response.json();
      setMusicTracks(payload.music || []);
      if (payload.track?.name) {
        setSelectedMusicName(payload.track.name);
        setAudioFile(null);
      }
      setMusicUrl('');
    } catch (caught) {
      setError(caught.message);
    } finally {
      setIsImportingMusic(false);
    }
  }

  async function exportReel() {
    if (isExporting || isExportingVideo) return;
    const seconds = Number(slideSeconds);
    const firstSeconds = Number(firstSlideSeconds);
    if (!Number.isFinite(seconds) || seconds < 0.5 || seconds > 20) {
      setError('Seconds per slide must be between 0.5 and 20.');
      return;
    }
    if (holdFirstSlide && (!Number.isFinite(firstSeconds) || firstSeconds < 0.5 || firstSeconds > 20)) {
      setError('First slide seconds must be between 0.5 and 20.');
      return;
    }
    setIsExportingVideo(true);
    setError('');
    setReelMessage('');
    try {
      const body = new FormData();
      body.set('slides', JSON.stringify(slides.map(toApiSlide)));
      body.set('seconds_per_slide', String(slideSeconds));
      body.set('crossfade_seconds', transition === 'fade' ? String(fadeSeconds) : '0');
      if (holdFirstSlide) body.set('first_slide_seconds', String(firstSlideSeconds));
      if (audioFile) body.set('audio', audioFile, audioFile.name);
      else if (selectedMusicName) body.set('music_name', selectedMusicName);
      const response = await fetch('/api/text-slideshow/export-video', {
        method: 'POST',
        body,
      });
      if (!response.ok) throw new Error(await readError(response));
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = 'video.mp4';
      link.click();
      URL.revokeObjectURL(url);
      const duration = Number(response.headers.get('x-video-duration'));
      const folder = response.headers.get('x-output-folder');
      const length = Number.isFinite(duration) ? `${duration.toFixed(2)}s` : 'the Reel';
      setReelMessage(
        folder
          ? `Saved ${length} as video.mp4 in outputs/${folder}.`
          : `Downloaded ${length}.`,
      );
    } catch (caught) {
      setError(caught.message);
    } finally {
      setIsExportingVideo(false);
    }
  }

  const previewUrl = slide ? previews[slide.id] : '';
  const fadeForEstimate = transition === 'fade' ? Number(fadeSeconds) || 0 : 0;
  const estimatedLength = reelDuration(
    slides.length,
    Number(slideSeconds) || 0,
    holdFirstSlide ? Number(firstSlideSeconds) || 0 : null,
    fadeForEstimate,
  );

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          {onHome ? (
            <button className="back-home-button" onClick={onHome} type="button">
              <ArrowLeft size={16} />
              Home
            </button>
          ) : null}
          <Type size={22} strokeWidth={2.2} />
          <h1>Text Slideshow</h1>
        </div>
        <div className="topbar-metrics" aria-label="slide size">
          <span>{slides.length} slides</span>
          <span>1080×1920</span>
          <span>PNG · Reel</span>
        </div>
      </header>

      <main className="tsl-layout">
        <section className="tsl-column" aria-label="slides">
          {error ? <div className="error-banner" role="alert">{error}</div> : null}
          <div className="slideshow-section">
            <div className="slideshow-heading">
              <Type size={16} />
              <div>
                <h2>Slides</h2>
                <span>Add, duplicate, and reorder. Export PNGs or a Reel.</span>
              </div>
            </div>
            <ol className="tsl-slides">
              {slides.map((item, index) => (
                <li className={index === selected ? 'is-active' : ''} key={item.id}>
                  <button
                    className="tsl-slide-button"
                    onClick={() => {
                      setSelected(index);
                      setSelectedElementId('');
                    }}
                    type="button"
                  >
                    <span>{index + 1}</span>
                    <strong>{slideSnippet(item, index)}</strong>
                  </button>
                  <div className="tsl-slide-actions">
                    <button aria-label="Move slide up" onClick={() => moveSlide(index, -1)} type="button">
                      <ArrowUp size={14} />
                    </button>
                    <button aria-label="Move slide down" onClick={() => moveSlide(index, 1)} type="button">
                      <ArrowDown size={14} />
                    </button>
                    <button aria-label="Duplicate slide" onClick={() => duplicateSlide(index)} type="button">
                      <Copy size={14} />
                    </button>
                    <button aria-label="Delete slide" onClick={() => deleteSlide(index)} type="button">
                      <Trash2 size={14} />
                    </button>
                  </div>
                </li>
              ))}
            </ol>
            <button
              className="secondary-button"
              onClick={() => {
                const next = makeSlide({
                  elements: [textElement({ text: `${slides.length + 1}. new slide` })],
                });
                setSlides((current) => [...current, next]);
                setSelected(slides.length);
                setSelectedElementId(next.elements[0].id);
              }}
              type="button"
            >
              <Plus size={14} />
              Add slide
            </button>
          </div>

          <div className="slideshow-section">
            <div className="slideshow-heading">
              <Film size={16} />
              <div>
                <h2>Reel</h2>
                <span>1080×1920, 30fps, H.264. Hard cuts unless you crossfade.</span>
              </div>
            </div>
            <div className="tsl-reel">
              <label>
                Seconds per slide
                <input
                  max="20"
                  min="0.5"
                  onChange={(event) => setSlideSeconds(event.target.value)}
                  step="0.05"
                  type="number"
                  value={slideSeconds}
                />
              </label>
              <label className="tsl-check">
                <input
                  checked={holdFirstSlide}
                  onChange={(event) => setHoldFirstSlide(event.target.checked)}
                  type="checkbox"
                />
                Hold the first slide longer
              </label>
              {holdFirstSlide ? (
                <label>
                  First slide seconds
                  <input
                    max="20"
                    min="0.5"
                    onChange={(event) => setFirstSlideSeconds(event.target.value)}
                    step="0.05"
                    type="number"
                    value={firstSlideSeconds}
                  />
                </label>
              ) : null}
              <label>
                Transition
                <select onChange={(event) => setTransition(event.target.value)} value={transition}>
                  <option value="cut">Hard cut</option>
                  <option value="fade">Crossfade</option>
                </select>
              </label>
              {transition === 'fade' ? (
                <label>
                  Crossfade seconds
                  <input
                    max="1.5"
                    min="0.1"
                    onChange={(event) => setFadeSeconds(event.target.value)}
                    step="0.05"
                    type="number"
                    value={fadeSeconds}
                  />
                </label>
              ) : null}
              <div className="tsl-music">
                <span className="tsl-music-label">Music from URL</span>
                <div className="music-import-controls">
                  <div className="music-import-row">
                    <input
                      className="url-input"
                      disabled={isImportingMusic}
                      onChange={(event) => setMusicUrl(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter') {
                          event.preventDefault();
                          fetchMusicFromUrl();
                        }
                      }}
                      placeholder="Instagram reel or video URL"
                      type="url"
                      value={musicUrl}
                    />
                    <button
                      className="secondary-button"
                      disabled={isImportingMusic || !musicUrl.trim()}
                      onClick={fetchMusicFromUrl}
                      type="button"
                    >
                      {isImportingMusic ? <Loader2 className="spin" size={14} /> : <Download size={14} />}
                      Fetch MP3
                    </button>
                  </div>
                  <label className="instagram-login-option">
                    <input
                      checked={useChromeLogin}
                      disabled={isImportingMusic}
                      onChange={(event) => setUseChromeLogin(event.target.checked)}
                      type="checkbox"
                    />
                    <span>Use Chrome login for restricted posts</span>
                  </label>
                </div>
                <label>
                  Saved tracks
                  <select
                    onChange={(event) => {
                      setSelectedMusicName(event.target.value);
                      if (event.target.value) {
                        setAudioFile(null);
                        setError('');
                      }
                    }}
                    value={selectedMusicName}
                  >
                    <option value="">None</option>
                    {musicTracks.map((track) => (
                      <option key={track.name} value={track.name}>
                        {track.name}
                      </option>
                    ))}
                  </select>
                </label>
                {selectedTrack ? (
                  <audio
                    className="audio-preview"
                    controls
                    key={selectedTrack.name}
                    src={selectedTrack.url}
                  />
                ) : audioPreviewUrl ? (
                  <audio className="audio-preview" controls src={audioPreviewUrl} />
                ) : (
                  <div className="audio-placeholder">Fetch a URL or pick a saved MP3 to preview it.</div>
                )}
              </div>
              <div className="tsl-audio-row">
                <button
                  className="secondary-button"
                  onClick={() => audioInputRef.current?.click()}
                  type="button"
                >
                  <Upload size={14} />
                  {audioFile ? audioFile.name : 'Add audio file'}
                </button>
                {audioFile ? (
                  <button
                    className="secondary-button"
                    onClick={() => setAudioFile(null)}
                    type="button"
                  >
                    Remove
                  </button>
                ) : null}
                <input
                  accept="audio/*,.mp3,.m4a,.aac,.wav,.mp4,.mov"
                  hidden
                  onChange={(event) => {
                    const file = event.target.files?.[0] || null;
                    setAudioFile(file);
                    if (file) setSelectedMusicName('');
                    event.target.value = '';
                  }}
                  ref={audioInputRef}
                  type="file"
                />
              </div>
              <p className="tsl-reel-note">
                About {estimatedLength.toFixed(2)}s.
                {' '}
                {audioFile || selectedTrack
                  ? 'The track is looped or trimmed to the video, then fades out.'
                  : 'No audio still includes a silent track. Fetched audio is saved as MP3 in music/.'}
              </p>
            </div>
          </div>

          {slide ? (
            <div className="slideshow-section">
              <div className="slideshow-heading">
                <ImagePlus size={16} />
                <div>
                  <h2>Background</h2>
                  <span>Cover-cropped to the full slide. No dimming.</span>
                </div>
              </div>
              <button
                className="slideshow-dropzone"
                onClick={() => backgroundInputRef.current?.click()}
                onDragOver={(event) => event.preventDefault()}
                onDrop={(event) => {
                  event.preventDefault();
                  onBackgroundFile(event.dataTransfer.files?.[0]);
                }}
                type="button"
              >
                <Upload size={16} />
                {slide.backgroundAssetId ? 'Replace background photo' : 'Upload a background photo'}
              </button>
              <input
                accept="image/jpeg,image/png,image/webp,image/gif"
                hidden
                onChange={(event) => {
                  onBackgroundFile(event.target.files?.[0]);
                  event.target.value = '';
                }}
                ref={backgroundInputRef}
                type="file"
              />
              <div className="tsl-colors">
                <label>
                  Top
                  <input
                    onChange={(event) => patchSlide(selected, { backgroundColor: event.target.value })}
                    type="color"
                    value={slide.backgroundColor}
                  />
                </label>
                <label>
                  Bottom
                  <input
                    onChange={(event) => patchSlide(selected, { backgroundColor2: event.target.value })}
                    type="color"
                    value={slide.backgroundColor2 || slide.backgroundColor}
                  />
                </label>
              </div>
              {slide.backgroundAssetId ? (
                <button
                  className="secondary-button"
                  onClick={() => patchSlide(selected, { backgroundAssetId: null })}
                  type="button"
                >
                  Remove photo
                </button>
              ) : null}
            </div>
          ) : null}
        </section>

        <section className="tsl-stage" aria-label="preview">
          <div className="tsl-frame" ref={frameRef}>
            {previewUrl ? (
              <img alt="" draggable={false} src={previewUrl} />
            ) : (
              <div className="tsl-frame-empty">Rendering preview…</div>
            )}
            <div className="tsl-overlay">
              {slide?.elements.map((element) => {
                const height = estimateBlockHeight(element);
                const selectedBox = element.id === selectedElementId;
                return (
                  <div
                    className={selectedBox ? 'tsl-box is-selected' : 'tsl-box'}
                    key={element.id}
                    onPointerDown={(event) => startDrag(event, element, 'move')}
                    style={{
                      left: `${(element.x / FRAME_WIDTH) * 100}%`,
                      top: `${(element.y / FRAME_HEIGHT) * 100}%`,
                      width: `${(element.width / FRAME_WIDTH) * 100}%`,
                      height: `${(height / FRAME_HEIGHT) * 100}%`,
                    }}
                  >
                    {element.type === 'text' ? (
                      <>
                        <button
                          aria-label="Resize text width from the left"
                          className="tsl-handle tsl-handle-left"
                          onPointerDown={(event) => startDrag(event, element, 'resize-left')}
                          type="button"
                        />
                        <button
                          aria-label="Resize text width from the right"
                          className="tsl-handle tsl-handle-right"
                          onPointerDown={(event) => startDrag(event, element, 'resize-right')}
                          type="button"
                        />
                      </>
                    ) : (
                      <button
                        aria-label="Resize image"
                        className="tsl-handle tsl-handle-corner"
                        onPointerDown={(event) => startDrag(event, element, 'resize-corner')}
                        type="button"
                      />
                    )}
                  </div>
                );
              })}
            </div>
          </div>
          <div className="slideshow-stage-actions">
            <button
              className="primary-button"
              disabled={isExporting || isExportingVideo}
              onClick={exportZip}
              type="button"
            >
              {isExporting ? <Loader2 className="spin" size={16} /> : <Download size={16} />}
              Download PNGs
            </button>
            <button
              className="primary-button"
              disabled={isExporting || isExportingVideo}
              onClick={exportReel}
              type="button"
            >
              {isExportingVideo ? <Loader2 className="spin" size={16} /> : <Film size={16} />}
              Download Reel
            </button>
          </div>
          <p className="tsl-note">
            Drag a box to move it. Drag the side handles to change how wide the text wraps.
            The preview is the same render as the PNG export. Download Reel saves video.mp4
            in that deck’s output folder.
            {reelMessage ? ` ${reelMessage}` : ''}
          </p>
        </section>

        <section className="tsl-column" aria-label="element settings">
          <div className="slideshow-section">
            <div className="slideshow-heading">
              <Plus size={16} />
              <div>
                <h2>Add</h2>
                <span>Title and body start in the reference style.</span>
              </div>
            </div>
            <div className="tsl-add-grid">
              <button
                className="secondary-button"
                onClick={() => addElement(textElement({ text: 'new title' }))}
                type="button"
              >
                Add title
              </button>
              <button
                className="secondary-button"
                onClick={() => addElement(textElement({
                  text: 'body text',
                  y: 438,
                  fontWeight: 'medium',
                  fontSize: 43,
                }))}
                type="button"
              >
                Add body text
              </button>
              <button
                className="secondary-button"
                onClick={() => addElement(textElement({
                  text: 'label',
                  x: 517,
                  y: 798.5,
                  width: 560,
                  fontWeight: 'medium',
                  fontSize: 41,
                  color: '#ffffff',
                  align: 'left',
                  highlight: true,
                }))}
                type="button"
              >
                Add label
              </button>
              <button
                className="secondary-button"
                onClick={() => overlayInputRef.current?.click()}
                type="button"
              >
                Add image
              </button>
            </div>
            <input
              accept="image/jpeg,image/png,image/webp,image/gif"
              hidden
              onChange={(event) => {
                onOverlayFile(event.target.files?.[0]);
                event.target.value = '';
              }}
              ref={overlayInputRef}
              type="file"
            />
          </div>

          {selectedElement ? (
            <div className="slideshow-section">
              <div className="slideshow-heading">
                <Type size={16} />
                <div>
                  <h2>{selectedElement.type === 'text' ? 'Text box' : 'Image'}</h2>
                  <span>Later elements paint on top.</span>
                </div>
              </div>
              {selectedElement.type === 'text' ? (
                <>
                  <textarea
                    className="tsl-textarea"
                    onChange={(event) => patchElement(selected, selectedElement.id, { text: event.target.value })}
                    value={selectedElement.text}
                  />
                  <label className="tsl-field">
                    Font
                    <select
                      className="select-input"
                      onChange={(event) => patchElement(selected, selectedElement.id, { fontWeight: event.target.value })}
                      value={selectedElement.fontWeight}
                    >
                      {fonts.map((font) => (
                        <option key={font.id} value={font.id}>{font.name}</option>
                      ))}
                    </select>
                  </label>
                  <label className="tsl-field">
                    Size
                    <input
                      max="320"
                      min="8"
                      onChange={(event) => patchElement(selected, selectedElement.id, { fontSize: Number(event.target.value) })}
                      type="number"
                      value={selectedElement.fontSize}
                    />
                  </label>
                  <label className="tsl-field">
                    Color
                    <input
                      onChange={(event) => patchElement(selected, selectedElement.id, { color: event.target.value })}
                      type="color"
                      value={selectedElement.color}
                    />
                  </label>
                  <div className="tsl-align" role="group" aria-label="Alignment">
                    {[
                      ['left', AlignLeft],
                      ['center', AlignCenter],
                      ['right', AlignRight],
                    ].map(([align, Icon]) => (
                      <button
                        aria-label={`Align ${align}`}
                        className={selectedElement.align === align ? 'is-active' : ''}
                        key={align}
                        onClick={() => patchElement(selected, selectedElement.id, { align })}
                        type="button"
                      >
                        <Icon size={16} />
                      </button>
                    ))}
                  </div>
                  <label className="tsl-check">
                    <input
                      checked={selectedElement.highlight}
                      onChange={(event) => patchElement(selected, selectedElement.id, { highlight: event.target.checked })}
                      type="checkbox"
                    />
                    Black label behind the text
                  </label>
                  {selectedElement.highlight ? (
                    <label className="tsl-field">
                      Label color
                      <input
                        onChange={(event) => patchElement(selected, selectedElement.id, { highlightColor: event.target.value })}
                        type="color"
                        value={selectedElement.highlightColor}
                      />
                    </label>
                  ) : null}
                </>
              ) : (
                <>
                  <p className="slideshow-hint">{selectedElement.name}</p>
                  <label className="tsl-field">
                    Corner radius
                    <input
                      max="200"
                      min="0"
                      onChange={(event) => patchElement(selected, selectedElement.id, { cornerRadius: Number(event.target.value) })}
                      type="number"
                      value={selectedElement.cornerRadius}
                    />
                  </label>
                  <label className="tsl-check">
                    <input
                      checked={selectedElement.aspectLocked}
                      onChange={(event) => patchElement(selected, selectedElement.id, { aspectLocked: event.target.checked })}
                      type="checkbox"
                    />
                    Keep aspect ratio
                  </label>
                </>
              )}
              <div className="tsl-layer-row">
                <button className="secondary-button" onClick={() => moveElement(selectedElement.id, -1)} type="button">
                  Send back
                </button>
                <button className="secondary-button" onClick={() => moveElement(selectedElement.id, 1)} type="button">
                  Bring forward
                </button>
                <button className="secondary-button" onClick={() => deleteElement(selectedElement.id)} type="button">
                  <Trash2 size={14} />
                  Delete
                </button>
              </div>
            </div>
          ) : (
            <div className="slideshow-section">
              <p className="slideshow-hint">
                Select a text box or image on the preview to edit it. Title boxes use TikTok Sans SemiBold at 53px. Body boxes use Medium at 43px, with the same line spacing and 820px column as the reference carousel.
              </p>
            </div>
          )}
        </section>
      </main>
    </div>
  );
}
