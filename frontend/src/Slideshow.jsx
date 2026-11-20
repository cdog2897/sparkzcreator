import { useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowDown,
  ArrowLeft,
  ArrowUp,
  Download,
  ImagePlus,
  Images,
  Loader2,
  Music2,
  Pause,
  Play,
  Shuffle,
  Upload,
  X,
} from 'lucide-react';

const api = {
  async get(path) {
    const response = await fetch(path);
    if (!response.ok) throw new Error(await readError(response));
    return response.json();
  },
  async post(path, body) {
    const response = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error(await readError(response));
    return response.json();
  },
  async upload(path, file) {
    const response = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/octet-stream' },
      body: file,
    });
    if (!response.ok) throw new Error(await readError(response));
    return response.json();
  },
};

async function readError(response) {
  try {
    const payload = await response.json();
    return payload.detail || response.statusText;
  } catch {
    return response.statusText;
  }
}

// Same hold as slideshow.slide_frame_count: nearest frame, half rounds up.
function slideFrameCount(seconds, fps) {
  const value = Number(seconds);
  if (!(value > 0) || !(fps > 0)) return 0;
  return Math.max(1, Math.floor(value * fps + 0.5));
}

function formatSeconds(value) {
  if (!Number.isFinite(value)) return '0.000s';
  return `${value.toFixed(3)}s`;
}

function newImageId() {
  if (window.crypto?.randomUUID) return window.crypto.randomUUID();
  return `photo-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export default function Slideshow({ onHome }) {
  const [settings, setSettings] = useState(null);
  const [images, setImages] = useState([]);
  const [text, setText] = useState('oh to travel the world\nwith the love of my life');
  const [duration, setDuration] = useState('0.417');
  const [musicName, setMusicName] = useState('random');
  const [tracks, setTracks] = useState([]);
  const [previewIndex, setPreviewIndex] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [isRendering, setIsRendering] = useState(false);
  const [isUploadingMusic, setIsUploadingMusic] = useState(false);
  const [job, setJob] = useState(null);
  const [error, setError] = useState('');
  const [showRender, setShowRender] = useState(false);
  const [dragId, setDragId] = useState('');
  const fileInputRef = useRef(null);
  const musicInputRef = useRef(null);
  const phoneRef = useRef(null);
  const captionRef = useRef(null);
  const [phoneWidth, setPhoneWidth] = useState(360);

  const fps = settings?.fps || 24;
  const framesPerImage = slideFrameCount(duration, fps);
  const slideSeconds = framesPerImage / fps;
  const totalSeconds = images.length * slideSeconds;
  const referenceWidth = settings?.referenceWidth || 960;
  const referenceFontSize = settings?.fontSizeAtReference || 35;
  const lineHeight = (settings?.lineTopDeltaAtReference || 42) / referenceFontSize;
  const textColor = settings?.textColor || '#F2CD46';
  const selectedTrack = tracks.find((track) => track.name === musicName) || null;

  useEffect(() => {
    let mounted = true;
    Promise.all([api.get('/api/slideshow'), api.get('/api/music')])
      .then(([slideshow, music]) => {
        if (!mounted) return;
        setSettings(slideshow);
        setDuration(String(slideshow.defaultImageDuration));
        setTracks(music.music || []);
      })
      .catch((caught) => {
        if (mounted) setError(caught.message);
      });
    return () => {
      mounted = false;
    };
  }, []);

  const imagesRef = useRef(images);
  imagesRef.current = images;
  useEffect(() => {
    return () => {
      imagesRef.current.forEach((image) => URL.revokeObjectURL(image.url));
    };
  }, []);

  useEffect(() => {
    const phone = phoneRef.current;
    if (!phone || !('ResizeObserver' in window)) return undefined;
    const observer = new ResizeObserver(() => {
      setPhoneWidth(phone.clientWidth || 360);
    });
    observer.observe(phone);
    setPhoneWidth(phone.clientWidth || 360);
    return () => observer.disconnect();
  }, [showRender, images.length]);

  useEffect(() => {
    const caption = captionRef.current;
    if (!caption) return;
    const base = referenceFontSize * (phoneWidth / referenceWidth);
    let size = base;
    caption.style.fontSize = `${size}px`;
    const maxWidth = phoneWidth * 0.92;
    while (size > 10 && caption.scrollWidth > maxWidth) {
      size -= 1;
      caption.style.fontSize = `${size}px`;
    }
  }, [text, phoneWidth, referenceFontSize, referenceWidth, showRender]);

  useEffect(() => {
    if (!isPlaying || images.length < 2 || showRender) return undefined;
    const timer = window.setInterval(() => {
      setPreviewIndex((current) => (current + 1) % images.length);
    }, Math.max(50, slideSeconds * 1000));
    return () => window.clearInterval(timer);
  }, [isPlaying, images.length, slideSeconds, showRender]);

  useEffect(() => {
    if (!job || !['queued', 'running'].includes(job.status)) return undefined;
    const timer = window.setInterval(() => {
      api.get(`/api/jobs/${job.id}`)
        .then((payload) => setJob(payload.job))
        .catch((caught) => setError(caught.message));
    }, 800);
    return () => window.clearInterval(timer);
  }, [job]);

  useEffect(() => {
    if (job?.status === 'complete') {
      setIsRendering(false);
      setShowRender(true);
    }
    if (job?.status === 'error') {
      setIsRendering(false);
      setError(job.error || 'Render failed.');
    }
  }, [job]);

  const previewImage = images[previewIndex] || images[0] || null;
  const captionStyle = useMemo(() => ({
    color: textColor,
    fontSize: `${referenceFontSize * (phoneWidth / referenceWidth)}px`,
    lineHeight,
  }), [lineHeight, phoneWidth, referenceFontSize, referenceWidth, textColor]);

  function addFiles(fileList) {
    const next = [];
    for (const file of fileList) {
      if (!file.type.startsWith('image/')) continue;
      next.push({
        id: newImageId(),
        file,
        url: URL.createObjectURL(file),
        name: file.name,
      });
    }
    if (!next.length) {
      setError('Choose JPEG, PNG, or WebP photos.');
      return;
    }
    setError('');
    setShowRender(false);
    setImages((current) => [...current, ...next]);
  }

  function removeImage(id) {
    setImages((current) => {
      const image = current.find((item) => item.id === id);
      if (image) URL.revokeObjectURL(image.url);
      return current.filter((item) => item.id !== id);
    });
    setPreviewIndex(0);
  }

  function moveImage(index, targetIndex) {
    setImages((current) => {
      if (targetIndex < 0 || targetIndex >= current.length || targetIndex === index) {
        return current;
      }
      const next = current.slice();
      const [item] = next.splice(index, 1);
      next.splice(targetIndex, 0, item);
      return next;
    });
  }

  function shuffleImages() {
    setImages((current) => {
      const next = current.slice();
      for (let index = next.length - 1; index > 0; index -= 1) {
        const swap = Math.floor(Math.random() * (index + 1));
        [next[index], next[swap]] = [next[swap], next[index]];
      }
      return next;
    });
    setPreviewIndex(0);
  }

  async function uploadMusic(file) {
    if (!file) return;
    setIsUploadingMusic(true);
    setError('');
    try {
      const payload = await api.upload(
        `/api/music/upload?filename=${encodeURIComponent(file.name)}`,
        file,
      );
      setTracks(payload.music || []);
      if (payload.track?.name) setMusicName(payload.track.name);
    } catch (caught) {
      setError(caught.message);
    } finally {
      setIsUploadingMusic(false);
    }
  }

  async function renderVideo() {
    if (!images.length || isRendering) return;
    const requested = Number(duration);
    if (!Number.isFinite(requested) || requested <= 0) {
      setError('Enter an image duration in seconds.');
      return;
    }
    setError('');
    setIsRendering(true);
    setIsPlaying(false);
    setJob(null);
    const body = new FormData();
    images.forEach((image) => body.append('images', image.file, image.name));
    body.append('text', text);
    body.append('image_duration', String(requested));
    body.append('music_name', musicName || 'random');
    try {
      const response = await fetch('/api/slideshow/render', { method: 'POST', body });
      if (!response.ok) throw new Error(await readError(response));
      const payload = await response.json();
      setJob(payload.job);
    } catch (caught) {
      setIsRendering(false);
      setError(caught.message);
    }
  }

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
          <Images size={22} strokeWidth={2.2} />
          <h1>Fast Cut Slideshow</h1>
        </div>
        <div className="topbar-metrics" aria-label="slideshow timing">
          <span>{images.length} photos</span>
          <span>{formatSeconds(totalSeconds)}</span>
          <span>{settings ? `${settings.width}×${settings.height}` : '1920×1080'}</span>
        </div>
      </header>

      <main className="slideshow-layout">
        <section className="slideshow-controls" aria-label="slideshow settings">
          {error ? <div className="error-banner" role="alert">{error}</div> : null}

          <div className="slideshow-section">
            <div className="slideshow-heading">
              <ImagePlus size={16} />
              <div>
                <h2>Photos</h2>
                <span>Order is the cut order. Each photo is full-bleed and still.</span>
              </div>
            </div>
            <button
              className="slideshow-dropzone"
              onClick={() => fileInputRef.current?.click()}
              onDragOver={(event) => event.preventDefault()}
              onDrop={(event) => {
                event.preventDefault();
                addFiles(event.dataTransfer.files);
              }}
              type="button"
            >
              <Upload size={16} />
              Drop photos here, or click to choose several
            </button>
            <input
              accept="image/jpeg,image/png,image/webp,image/gif"
              hidden
              multiple
              onChange={(event) => {
                addFiles(event.target.files || []);
                event.target.value = '';
              }}
              ref={fileInputRef}
              type="file"
            />
            {images.length ? (
              <div className="slideshow-photo-tools">
                <button className="secondary-button" onClick={shuffleImages} type="button">
                  <Shuffle size={14} />
                  Shuffle
                </button>
                <span>{images.length} in the cut</span>
              </div>
            ) : null}
            <ol className="slideshow-photos">
              {images.map((image, index) => (
                <li
                  className={index === previewIndex ? 'is-active' : ''}
                  draggable
                  key={image.id}
                  onDragOver={(event) => event.preventDefault()}
                  onDragStart={() => setDragId(image.id)}
                  onDrop={(event) => {
                    event.preventDefault();
                    if (event.dataTransfer.files?.length) {
                      addFiles(event.dataTransfer.files);
                      return;
                    }
                    const from = images.findIndex((item) => item.id === dragId);
                    moveImage(from, index);
                    setDragId('');
                  }}
                >
                  <button
                    className="slideshow-thumb"
                    onClick={() => {
                      setPreviewIndex(index);
                      setShowRender(false);
                    }}
                    type="button"
                  >
                    <img alt="" src={image.url} />
                    <span>{index + 1}</span>
                  </button>
                  <div className="slideshow-photo-actions">
                    <button aria-label={`Move photo ${index + 1} earlier`} onClick={() => moveImage(index, index - 1)} type="button">
                      <ArrowUp size={14} />
                    </button>
                    <button aria-label={`Move photo ${index + 1} later`} onClick={() => moveImage(index, index + 1)} type="button">
                      <ArrowDown size={14} />
                    </button>
                    <button aria-label={`Remove photo ${index + 1}`} onClick={() => removeImage(image.id)} type="button">
                      <X size={14} />
                    </button>
                  </div>
                </li>
              ))}
            </ol>
          </div>

          <div className="slideshow-section">
            <div className="slideshow-heading">
              <div>
                <h2>Text</h2>
                <span>Centered, static, no shadow. Use a new line for the second line.</span>
              </div>
            </div>
            <textarea
              className="caption-input slideshow-text"
              onChange={(event) => setText(event.target.value)}
              placeholder={'oh to travel the world\nwith the love of my life'}
              rows={3}
              value={text}
            />
          </div>

          <div className="slideshow-section">
            <div className="slideshow-heading">
              <div>
                <h2>Cut length</h2>
                <span>Snaps to whole frames. The default is the TikTok hold.</span>
              </div>
            </div>
            <label className="slideshow-duration">
              <span>Seconds per photo</span>
              <input
                max={settings?.maxImageDuration || 5}
                min={settings?.minImageDuration || 0.05}
                onChange={(event) => setDuration(event.target.value)}
                step="0.001"
                type="number"
                value={duration}
              />
            </label>
            <p className="slideshow-hint">
              {framesPerImage} frames at {fps} fps · {formatSeconds(slideSeconds)} per photo
              {images.length ? ` · ${formatSeconds(totalSeconds)} total` : ''}
            </p>
          </div>

          <div className="slideshow-section">
            <div className="slideshow-heading">
              <Music2 size={16} />
              <div>
                <h2>Music</h2>
                <span>Trimmed to the video. Random uses a track from music/.</span>
              </div>
            </div>
            <div className="slideshow-music-row">
              <select
                className="select-input"
                onChange={(event) => setMusicName(event.target.value)}
                value={musicName}
              >
                <option value="random">Random track</option>
                <option value="none">No music</option>
                {tracks.map((track) => (
                  <option key={track.name} value={track.name}>
                    {track.name} ({Number(track.duration).toFixed(2)}s)
                  </option>
                ))}
              </select>
              <button
                className="secondary-button"
                disabled={isUploadingMusic}
                onClick={() => musicInputRef.current?.click()}
                type="button"
              >
                {isUploadingMusic ? <Loader2 className="spin" size={14} /> : <Upload size={14} />}
                Upload
              </button>
              <input
                accept="audio/*,.mp3,.m4a,.wav,.aac"
                hidden
                onChange={(event) => {
                  uploadMusic(event.target.files?.[0]);
                  event.target.value = '';
                }}
                ref={musicInputRef}
                type="file"
              />
            </div>
            {selectedTrack ? (
              <audio className="audio-preview" controls src={selectedTrack.url} />
            ) : (
              <p className="slideshow-hint">
                {musicName === 'none'
                  ? 'This render will be silent.'
                  : tracks.length
                    ? 'A track is picked from the library when you render.'
                    : 'music/ is empty, so a random choice renders silent until you upload a track.'}
              </p>
            )}
          </div>

          <div className="render-actions">
            <button
              className="primary-button"
              disabled={!images.length || isRendering}
              onClick={renderVideo}
              type="button"
            >
              {isRendering ? <Loader2 className="spin" size={16} /> : <Images size={16} />}
              {isRendering ? 'Rendering' : 'Render MP4'}
            </button>
            {job?.status === 'complete' && job.outputUrl ? (
              <a className="output-link" download href={job.outputUrl}>
                <Download size={15} />
                Download
              </a>
            ) : null}
          </div>
          {job?.logs?.length ? (
            <p className="slideshow-hint">{job.logs[job.logs.length - 1]}</p>
          ) : null}
        </section>

        <section className="slideshow-stage" aria-label="slideshow preview">
          <div
            className="slideshow-phone"
            ref={phoneRef}
            style={{
              aspectRatio: settings
                ? `${settings.width} / ${settings.height}`
                : '16 / 9',
            }}
          >
            {showRender && job?.outputUrl ? (
              <video
                controls
                key={job.outputUrl}
                playsInline
                src={job.outputUrl}
              />
            ) : previewImage ? (
              <img alt="" src={previewImage.url} />
            ) : (
              <div className="slideshow-empty">Drop photos to preview the cut.</div>
            )}
            {text.trim() && !(showRender && job?.outputUrl) ? (
              <p className="slideshow-caption" ref={captionRef} style={captionStyle}>
                {text}
              </p>
            ) : null}
          </div>
          <div className="slideshow-stage-actions">
            <button
              className="secondary-button"
              disabled={images.length < 2 || showRender}
              onClick={() => setIsPlaying((current) => !current)}
              type="button"
            >
              {isPlaying ? <Pause size={14} /> : <Play size={14} />}
              {isPlaying ? 'Pause cuts' : 'Play cuts'}
            </button>
            {job?.outputUrl ? (
              <button
                className="secondary-button"
                onClick={() => setShowRender((current) => !current)}
                type="button"
              >
                {showRender ? 'Photo preview' : 'Rendered video'}
              </button>
            ) : null}
          </div>
        </section>
      </main>
    </div>
  );
}
