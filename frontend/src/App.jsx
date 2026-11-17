import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  ArrowLeft,
  Check,
  Download,
  ChevronRight,
  Film,
  Folder,
  Image as ImageIcon,
  Loader2,
  Music2,
  Pause,
  Play,
  Plus,
  Scissors,
  Type,
  Upload,
  Wand2,
  X,
} from 'lucide-react';

const api = {
  async get(path, options) {
    const response = await fetch(path, options);
    if (!response.ok) {
      throw new Error(await readError(response));
    }
    return response.json();
  },
  async post(path, body) {
    const response = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      throw new Error(await readError(response));
    }
    return response.json();
  },
  async upload(path, file) {
    const response = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/octet-stream' },
      body: file,
    });
    if (!response.ok) {
      throw new Error(await readError(response));
    }
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

function formatSeconds(value) {
  if (!Number.isFinite(value)) return '0.00s';
  return `${value.toFixed(2)}s`;
}

function clipDuration(clip) {
  const trimStart = Number(clip?.trimStartSeconds);
  const trimEnd = Number(clip?.trimEndSeconds);
  if (Number.isFinite(trimStart) && Number.isFinite(trimEnd)) {
    return Math.max(0, trimEnd - trimStart);
  }

  const duration = Number(clip?.duration);
  return Number.isFinite(duration) ? Math.max(0, duration) : 0;
}

function timelineClipId(clip) {
  return clip?.instanceId || clip?.key || '';
}

function sourceClipKey(clip) {
  return clip?.sourceKey || clip?.key || '';
}

function createTimelineClipId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `timeline-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function buildCoverTimeline(clips) {
  let start = 0;
  return clips.reduce((entries, clip) => {
    const duration = clipDuration(clip);
    if (duration <= 0) return entries;

    entries.push({ clip, duration, start, end: start + duration });
    start += duration;
    return entries;
  }, []);
}

function coverPositionAtOffset(timeline, requestedOffset) {
  if (!timeline.length) return null;

  const totalDuration = timeline[timeline.length - 1].end;
  const latestOffset = Math.max(0, totalDuration - (1 / 30));
  const offset = Math.min(latestOffset, Math.max(0, requestedOffset));
  const entry = timeline.find((item) => offset < item.end) || timeline[timeline.length - 1];
  const trimStart = Number(entry.clip.trimStartSeconds) || 0;
  const latestClipTime = Math.max(trimStart, trimStart + entry.duration - (1 / 30));

  return {
    ...entry,
    offset,
    timeSeconds: Math.min(
      latestClipTime,
      trimStart + Math.max(0, offset - entry.start),
    ),
  };
}

function coverOffsetForFrame(timeline, frame) {
  if (!frame) return 0;
  const entry = timeline.find((item) => timelineClipId(item.clip) === frame.clipKey);
  if (!entry) return 0;
  const trimStart = Number(entry.clip.trimStartSeconds) || 0;
  return entry.start + Math.min(
    Math.max(0, frame.timeSeconds - trimStart),
    entry.duration,
  );
}

function classNames(...values) {
  return values.filter(Boolean).join(' ');
}

const CLIP_SCALE_OPTIONS = [
  { value: 'fit', label: 'Fit' },
  { value: 'square', label: 'Square' },
  { value: 'fill', label: 'Fill' },
];
const TEXT_HORIZONTAL_PADDING = 240;
const MIN_TEXT_LINE_WIDTH = 120;
const CTA_TEXT = 'we highly recommend this app to find cute date ideas!';
const CTA_REFERENCE_WIDTH = 1080;
const CTA_REFERENCE_HEIGHT = 1350;
const CTA_APPSTORE_PADDING = 80;
const CTA_APPSTORE_WIDTH = 540;
const CTA_APPSTORE_ASPECT = 896 / 1206;
const CTA_TEXT_FONT_SIZE = 50;
const CTA_TEXT_TOP_PADDING = 34;
const CTA_TEXT_MAX_WIDTH = CTA_APPSTORE_WIDTH;
const MEDIA_THUMBNAIL_CONCURRENCY = 3;
const MIN_TIMELINE_CLIP_SECONDS = 0.1;
const TIMELINE_FRAME_SECONDS = 1 / 30;
const mediaThumbnailCache = new Map();
const mediaThumbnailQueue = [];
let activeMediaThumbnailLoads = 0;

function runNextMediaThumbnailLoads() {
  while (
    activeMediaThumbnailLoads < MEDIA_THUMBNAIL_CONCURRENCY
    && mediaThumbnailQueue.length
  ) {
    const entry = mediaThumbnailQueue.shift();
    if (entry.cancelled) continue;

    activeMediaThumbnailLoads += 1;
    entry
      .start()
      .catch(() => {})
      .finally(() => {
        activeMediaThumbnailLoads -= 1;
        runNextMediaThumbnailLoads();
      });
  }
}

function queueMediaThumbnail(start) {
  const entry = { cancelled: false, start };
  mediaThumbnailQueue.push(entry);
  runNextMediaThumbnailLoads();

  return () => {
    entry.cancelled = true;
  };
}

function boundedPercent(value) {
  if (!Number.isFinite(value)) return 50;
  return Math.min(100, Math.max(0, value));
}

function videoElementMetadata(video) {
  return {
    duration: Number.isFinite(video.duration) ? video.duration : 0,
    width: video.videoWidth || null,
    height: video.videoHeight || null,
  };
}

function clipWithDefaultAttributes(clip) {
  return {
    ...clip,
    scaleMode: clip.scaleMode || 'fit',
    cropXPercent: boundedPercent(clip.cropXPercent ?? 50),
  };
}

function clipUsesHorizontalCrop(clip) {
  return ['square', 'fill'].includes(clip?.scaleMode);
}

function clipThumbnailStyle(clip) {
  const ratio = clip.width && clip.height ? clip.width / clip.height : 16 / 9;
  const maxWidth = 96;
  const maxHeight = 74;

  if (ratio >= maxWidth / maxHeight) {
    return {
      width: `${maxWidth}px`,
      height: `${Math.round(maxWidth / ratio)}px`,
    };
  }

  return {
    width: `${Math.round(maxHeight * ratio)}px`,
    height: `${maxHeight}px`,
  };
}

function MediaClipThumbnail({ clip, onMetadata }) {
  const cached = mediaThumbnailCache.get(clip.key);
  const [loadState, setLoadState] = useState(cached ? 'ready' : 'idle');
  const [thumbnailUrl, setThumbnailUrl] = useState(cached?.url || '');
  const [dimensions, setDimensions] = useState(
    cached?.metadata || {
      width: clip.width,
      height: clip.height,
    },
  );
  const containerRef = useRef(null);
  const videoRef = useRef(null);
  const finishQueueRef = useRef(null);
  const captureStartedRef = useRef(false);

  function finishQueue() {
    finishQueueRef.current?.();
    finishQueueRef.current = null;
  }

  function reportMetadata(video) {
    const metadata = videoElementMetadata(video);
    setDimensions(metadata);
    onMetadata(clip.key, metadata);
    return metadata;
  }

  function captureThumbnail(video) {
    if (captureStartedRef.current || !video.videoWidth || !video.videoHeight) return;
    captureStartedRef.current = true;

    const scale = Math.min(1, 192 / video.videoWidth, 148 / video.videoHeight);
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(video.videoWidth * scale));
    canvas.height = Math.max(1, Math.round(video.videoHeight * scale));
    const context = canvas.getContext('2d');

    if (!context) {
      setLoadState('error');
      finishQueue();
      return;
    }

    context.drawImage(video, 0, 0, canvas.width, canvas.height);
    const metadata = reportMetadata(video);
    canvas.toBlob(
      (blob) => {
        if (!blob) {
          setLoadState('error');
          finishQueue();
          return;
        }

        const url = URL.createObjectURL(blob);
        mediaThumbnailCache.set(clip.key, { metadata, url });
        setThumbnailUrl(url);
        setLoadState('ready');
        finishQueue();
      },
      'image/jpeg',
      0.78,
    );
  }

  function handleLoadedMetadata(event) {
    const video = event.currentTarget;
    reportMetadata(video);
    const seekTime = Number.isFinite(video.duration)
      ? Math.min(0.25, Math.max(0, video.duration * 0.04))
      : 0;

    if (seekTime > 0.01) {
      video.currentTime = seekTime;
    }
  }

  function handleLoadedData(event) {
    if (event.currentTarget.currentTime <= 0.01) {
      captureThumbnail(event.currentTarget);
    }
  }

  function handleLoadError() {
    setLoadState('error');
    finishQueue();
  }

  useEffect(() => {
    const cachedThumbnail = mediaThumbnailCache.get(clip.key);
    if (cachedThumbnail) {
      onMetadata(clip.key, cachedThumbnail.metadata);
      return undefined;
    }

    const element = containerRef.current;
    if (!element || !('IntersectionObserver' in window)) {
      setLoadState('queued');
      return queueMediaThumbnail(
        () => new Promise((resolve) => {
          finishQueueRef.current = resolve;
          setLoadState('loading');
        }),
      );
    }

    let cancelQueuedLoad;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (!entry.isIntersecting) return;
        observer.disconnect();
        setLoadState('queued');
        cancelQueuedLoad = queueMediaThumbnail(
          () => new Promise((resolve) => {
            finishQueueRef.current = resolve;
            setLoadState('loading');
          }),
        );
      },
      {
        root: element.closest('.media-browser'),
        rootMargin: '160px 0px',
      },
    );
    observer.observe(element);

    return () => {
      observer.disconnect();
      cancelQueuedLoad?.();
      finishQueue();
    };
  }, [clip.key, onMetadata]);

  useEffect(() => {
    if (loadState !== 'loading') return undefined;

    const timeout = window.setTimeout(() => {
      videoRef.current?.load();
    }, 0);

    return () => window.clearTimeout(timeout);
  }, [loadState]);

  return (
    <span
      className={classNames('media-thumb-icon', `is-${loadState}`)}
      ref={containerRef}
      style={clipThumbnailStyle(dimensions)}
    >
      {thumbnailUrl ? <img alt="" decoding="async" src={thumbnailUrl} /> : null}
      {loadState === 'loading' ? (
        <video
          className="media-thumb-loader-video"
          muted
          onError={handleLoadError}
          onLoadedData={handleLoadedData}
          onLoadedMetadata={handleLoadedMetadata}
          onSeeked={(event) => captureThumbnail(event.currentTarget)}
          playsInline
          preload="metadata"
          ref={videoRef}
          src={clip.url}
        />
      ) : null}
      {['idle', 'queued', 'loading'].includes(loadState) ? (
        <span className="media-thumb-loading" title="Loading thumbnail">
          <Loader2 className="spin" size={18} />
        </span>
      ) : null}
      {loadState === 'error' ? <Film className="media-thumb-fallback" size={25} /> : null}
    </span>
  );
}

function previewVideoBoxStyle(clip, frameSize) {
  if (clip?.scaleMode === 'square') {
    const side = Math.min(frameSize.width, frameSize.height);
    return {
      width: `${side}px`,
      height: `${side}px`,
    };
  }

  return {
    width: '100%',
    height: '100%',
  };
}

function previewVideoStyle(clip) {
  const scaleMode = clip?.scaleMode || 'fit';
  const cropXPercent = boundedPercent(clip?.cropXPercent ?? 50);

  return {
    objectFit: scaleMode === 'fit' ? 'contain' : 'cover',
    objectPosition: `${cropXPercent}% center`,
  };
}

function trimPreviewAspectRatio(clip, videoFormat) {
  const scaleMode = clip?.scaleMode || 'fit';

  if (scaleMode === 'square') return 1;
  if (scaleMode === 'fill') {
    const formatWidth = Number(videoFormat?.width) || 1080;
    const formatHeight = Number(videoFormat?.height) || 1920;
    return formatWidth / formatHeight;
  }

  const sourceAspectRatio = Number(clip?.aspectRatio);
  if (Number.isFinite(sourceAspectRatio) && sourceAspectRatio > 0) {
    return sourceAspectRatio;
  }

  const sourceWidth = Number(clip?.width);
  const sourceHeight = Number(clip?.height);
  if (sourceWidth > 0 && sourceHeight > 0) {
    return sourceWidth / sourceHeight;
  }

  return 16 / 9;
}

function maxTextHorizontalPadding(videoFormat) {
  const formatWidth = videoFormat?.width || 1080;
  return Math.max(0, Math.floor((formatWidth - MIN_TEXT_LINE_WIDTH) / 2));
}

function boundedTextHorizontalPadding(value, videoFormat) {
  if (!Number.isFinite(value)) return TEXT_HORIZONTAL_PADDING;
  return Math.min(maxTextHorizontalPadding(videoFormat), Math.max(0, value));
}

function previewTextStyle(
  videoFormat,
  frameSize,
  outputFontSize,
  textYPercent,
  textHorizontalPadding,
) {
  const formatWidth = videoFormat?.width || 1080;
  const scale = frameSize.width / formatWidth;
  const boundedPadding = boundedTextHorizontalPadding(textHorizontalPadding, videoFormat);
  const maxOutputTextWidth = Math.max(1, formatWidth - (boundedPadding * 2));

  return {
    fontSize: `${outputFontSize * scale}px`,
    lineHeight: `${outputFontSize * scale}px`,
    maxWidth: `${maxOutputTextWidth * scale}px`,
    top: `${textYPercent}%`,
    width: `${maxOutputTextWidth * scale}px`,
  };
}

function previewCtaLayout(videoFormat, frameSize) {
  const formatWidth = videoFormat?.width || CTA_REFERENCE_WIDTH;
  const formatHeight = videoFormat?.height || 1920;
  const frameScale = frameSize.width / formatWidth;
  const layoutScale = Math.min(
    formatWidth / CTA_REFERENCE_WIDTH,
    formatHeight / CTA_REFERENCE_HEIGHT,
  );
  const outputToPreviewScale = layoutScale * frameScale;
  const padding = CTA_APPSTORE_PADDING * outputToPreviewScale;
  const imageWidth = CTA_APPSTORE_WIDTH * outputToPreviewScale;
  const imageHeight = imageWidth * CTA_APPSTORE_ASPECT;

  return {
    image: {
      left: `${padding}px`,
      top: `${padding}px`,
      width: `${imageWidth}px`,
    },
    text: {
      fontSize: `${CTA_TEXT_FONT_SIZE * outputToPreviewScale}px`,
      left: `${padding}px`,
      lineHeight: `${CTA_TEXT_FONT_SIZE * outputToPreviewScale}px`,
      maxWidth: `${CTA_TEXT_MAX_WIDTH * outputToPreviewScale}px`,
      textShadow: `${2 * outputToPreviewScale}px ${2 * outputToPreviewScale}px ${6 * outputToPreviewScale}px rgba(0, 0, 0, 0.45)`,
      top: `${padding + imageHeight + CTA_TEXT_TOP_PADDING * outputToPreviewScale}px`,
      width: `${CTA_TEXT_MAX_WIDTH * outputToPreviewScale}px`,
    },
  };
}

function App({ onHome }) {
  const [bootstrap, setBootstrap] = useState(null);
  const [formats, setFormats] = useState([]);
  const [mediaPool, setMediaPool] = useState(null);
  const [folders, setFolders] = useState([]);
  const [clips, setClips] = useState([]);
  const [selectedFormatOption, setSelectedFormatOption] = useState(null);
  const [selectedFolderPath, setSelectedFolderPath] = useState('');
  const [timelineClips, setTimelineClips] = useState([]);
  const [activeClipKey, setActiveClipKey] = useState('');
  const [timelinePreviewClipKey, setTimelinePreviewClipKey] = useState('');
  const [isTimelinePreviewPlaying, setIsTimelinePreviewPlaying] = useState(false);
  const [selectedMusicName, setSelectedMusicName] = useState('');
  const [musicImportUrl, setMusicImportUrl] = useState('');
  const [useInstagramLogin, setUseInstagramLogin] = useState(false);
  const [isUploadingMusic, setIsUploadingMusic] = useState(false);
  const [deletingMusicName, setDeletingMusicName] = useState('');
  const [shouldAutoplayMusic, setShouldAutoplayMusic] = useState(false);
  const [text, setText] = useState('');
  const [textYPercent, setTextYPercent] = useState(50);
  const [textHorizontalPadding, setTextHorizontalPadding] = useState(TEXT_HORIZONTAL_PADDING);
  const [fontSize, setFontSize] = useState(60);
  const [addCta, setAddCta] = useState(false);
  const [ctaText, setCtaText] = useState(CTA_TEXT);
  const [selectedSettingsTab, setSelectedSettingsTab] = useState('format');
  const [selectedRenderSubTab, setSelectedRenderSubTab] = useState('video');
  const [draggedClipKey, setDraggedClipKey] = useState('');
  const [dragOverClipKey, setDragOverClipKey] = useState('');
  const [hoveredMediaClipKey, setHoveredMediaClipKey] = useState('');
  const [previewFrameSize, setPreviewFrameSize] = useState({
    width: 320,
    height: 320 * (16 / 9),
  });
  const [job, setJob] = useState(null);
  const [coverFrame, setCoverFrame] = useState(null);
  const [coverOutput, setCoverOutput] = useState(null);
  const [isRenderingCover, setIsRenderingCover] = useState(false);
  const [isCoverPickerOpen, setIsCoverPickerOpen] = useState(false);
  const [coverPickerOffset, setCoverPickerOffset] = useState(0);
  const [coverCropXPercent, setCoverCropXPercent] = useState(50);
  const [trimEditor, setTrimEditor] = useState(null);
  const [trimPreviewTime, setTrimPreviewTime] = useState(0);
  const [trimHoverTime, setTrimHoverTime] = useState(0);
  const [isTrimTimelineHovered, setIsTrimTimelineHovered] = useState(false);
  const [isTrimPreviewPlaying, setIsTrimPreviewPlaying] = useState(false);
  const [activeTrimBracket, setActiveTrimBracket] = useState('');
  const [isLoadingMedia, setIsLoadingMedia] = useState(false);
  const [isImportingMusic, setIsImportingMusic] = useState(false);
  const [error, setError] = useState('');
  const previewPanelRef = useRef(null);
  const previewVideoRef = useRef(null);
  const musicAudioRef = useRef(null);
  const coverPickerVideoRef = useRef(null);
  const trimVideoRef = useRef(null);
  const trimScrubAnimationRef = useRef(null);
  const pendingTrimScrubTimeRef = useRef(0);

  const selectedFormat = useMemo(
    () => formats.find((format) => format.option === selectedFormatOption),
    [formats, selectedFormatOption],
  );

  const selectedClips = timelineClips;

  const coverTimeline = useMemo(
    () => buildCoverTimeline(selectedClips),
    [selectedClips],
  );
  const coverTimelineSeconds = coverTimeline[coverTimeline.length - 1]?.end || 0;
  const draftCoverPosition = useMemo(
    () => coverPositionAtOffset(coverTimeline, coverPickerOffset),
    [coverPickerOffset, coverTimeline],
  );
  const selectedCoverClip = useMemo(
    () => (
      selectedClips.find((clip) => timelineClipId(clip) === coverFrame?.clipKey) || null
    ),
    [coverFrame, selectedClips],
  );

  const activeClip = useMemo(() => {
    return timelineClips.find((clip) => timelineClipId(clip) === activeClipKey) || null;
  }, [activeClipKey, timelineClips]);

  const hoveredMediaClip = useMemo(() => (
    clips.find((clip) => sourceClipKey(clip) === hoveredMediaClipKey) || null
  ), [clips, hoveredMediaClipKey]);

  const timelinePreviewClip = useMemo(() => (
    timelineClips.find((clip) => timelineClipId(clip) === timelinePreviewClipKey) || null
  ), [timelineClips, timelinePreviewClipKey]);

  const centerPreviewClip = useMemo(() => (
    timelinePreviewClip
      || (hoveredMediaClip ? clipWithDefaultAttributes(hoveredMediaClip) : activeClip)
  ), [activeClip, hoveredMediaClip, timelinePreviewClip]);
  const isMediaPoolPreview = Boolean(hoveredMediaClip && !timelinePreviewClip);

  const selectedMusic = useMemo(
    () => bootstrap?.music?.find((track) => track.name === selectedMusicName),
    [bootstrap, selectedMusicName],
  );
  const musicTracks = bootstrap?.music || [];

  const hydrateClipMetadata = useCallback((clipKey, metadata) => {
    const updateClip = (clip) => (
      sourceClipKey(clip) === clipKey
        ? {
            ...clip,
            duration: metadata.duration,
            width: metadata.width,
            height: metadata.height,
            aspectRatio: (
              metadata.width && metadata.height
                ? metadata.width / metadata.height
                : clip.aspectRatio
            ),
          }
        : clip
    );

    setClips((current) => current.map(updateClip));
    setTimelineClips((current) => current.map(updateClip));
  }, []);

  const totalSelectedClipSeconds = selectedClips.reduce(
    (sum, clip) => sum + clipDuration(clip),
    0,
  );
  const selectedMusicSeconds = Number(selectedMusic?.duration) || 0;
  const estimatedOutputSeconds = Math.min(
    totalSelectedClipSeconds,
    selectedMusicSeconds || totalSelectedClipSeconds,
  );
  const timelineScaleSeconds = Math.max(
    totalSelectedClipSeconds,
    selectedMusicSeconds,
    MIN_TIMELINE_CLIP_SECONDS,
  );
  const timelineCanvasPixels = Math.max(
    360,
    timelineScaleSeconds * 36,
    selectedClips.length * 68,
  );
  const songWidthPercent = selectedMusicSeconds
    ? (selectedMusicSeconds / timelineScaleSeconds) * 100
    : 0;
  const clipsExceedSong = Boolean(
    selectedMusicSeconds
    && totalSelectedClipSeconds > selectedMusicSeconds + 0.01,
  );
  const durationDifferenceSeconds = Math.abs(
    selectedMusicSeconds - totalSelectedClipSeconds,
  );
  const trimEditorThumbnailUrl = trimEditor
    ? mediaThumbnailCache.get(sourceClipKey(trimEditor.clip))?.url || ''
    : '';
  const trimSelectionStage = trimEditor?.selectionStage || 'complete';
  const isChoosingTrimStart = trimSelectionStage === 'start';
  const isChoosingTrimEnd = trimSelectionStage === 'end';
  const isTrimSelectionComplete = trimSelectionStage === 'complete';
  const provisionalTrimEndSeconds = trimEditor
    ? (
      isChoosingTrimEnd
        ? Math.max(trimEditor.startSeconds, trimHoverTime)
        : trimEditor.endSeconds
    )
    : 0;
  const trimPlaybackEndSeconds = trimEditor
    ? (isChoosingTrimEnd ? provisionalTrimEndSeconds : trimEditor.endSeconds)
    : 0;
  const displayedTrimDuration = trimEditor && !isChoosingTrimStart
    ? Math.max(0, trimPlaybackEndSeconds - trimEditor.startSeconds)
    : null;
  const hasPlayableTrimSelection = Boolean(
    trimEditor
    && (
      isChoosingTrimStart
        ? trimEditor.duration >= MIN_TIMELINE_CLIP_SECONDS - 0.001
        : trimPlaybackEndSeconds - trimEditor.startSeconds
          >= MIN_TIMELINE_CLIP_SECONDS - 0.001
    ),
  );
  const canSaveTrimSelection = Boolean(
    trimEditor
    && trimEditor.duration > 0
    && (
      isChoosingTrimStart
      || (
        isTrimSelectionComplete
        && trimEditor.endSeconds - trimEditor.startSeconds
          >= MIN_TIMELINE_CLIP_SECONDS - 0.001
      )
    ),
  );
  const settingsTabs = [
    { id: 'format', label: 'Format', icon: <Scissors size={15} /> },
    { id: 'text', label: 'Text', icon: <Type size={15} /> },
    { id: 'music', label: 'Music', icon: <Music2 size={15} /> },
    { id: 'render', label: 'Render', icon: <Wand2 size={15} /> },
  ];

  useEffect(() => {
    let isMounted = true;
    api
      .get('/api/bootstrap')
      .then((payload) => {
        if (!isMounted) return;
        setBootstrap(payload);
        setFormats(payload.formats || []);
        setSelectedFormatOption((payload.formats || [])[0]?.option || null);
        setSelectedMusicName('');
        setShouldAutoplayMusic(false);
        const defaultTextSize = payload.textSizes?.find((option) => option.default);
        if (defaultTextSize) {
          setFontSize(defaultTextSize.fontSize);
        }
      })
      .catch((caught) => setError(caught.message));
    return () => {
      isMounted = false;
    };
  }, []);

  useEffect(() => {
    let isMounted = true;
    const controller = new AbortController();
    setIsLoadingMedia(true);
    setError('');
    setFolders([]);
    setClips([]);
    const params = new URLSearchParams();
    if (selectedFolderPath) {
      params.set('path', selectedFolderPath);
    }
    api
      .get(
        `/api/media-pool?${params.toString()}`,
        { signal: controller.signal },
      )
      .then((payload) => {
        if (!isMounted) return;
        const nextClips = payload.clips || [];
        setMediaPool(payload);
        setFolders(payload.folders || []);
        setClips(nextClips);
      })
      .catch((caught) => {
        if (caught.name !== 'AbortError') {
          setError(caught.message);
        }
      })
      .finally(() => {
        if (isMounted) setIsLoadingMedia(false);
      });
    return () => {
      isMounted = false;
      controller.abort();
    };
  }, [selectedFolderPath]);

  useEffect(() => {
    if (!job || !['queued', 'running'].includes(job.status)) return undefined;
    const interval = window.setInterval(() => {
      api
        .get(`/api/jobs/${job.id}`)
        .then((payload) => {
          setJob(payload.job);
        })
        .catch((caught) => setError(caught.message));
    }, 1200);
    return () => window.clearInterval(interval);
  }, [job]);

  useEffect(() => {
    if (
      coverFrame
      && !selectedClips.some((clip) => timelineClipId(clip) === coverFrame.clipKey)
    ) {
      setCoverFrame(null);
      setCoverOutput(null);
    }
  }, [coverFrame, selectedClips]);

  useEffect(() => {
    if (!timelinePreviewClipKey) return;
    if (timelinePreviewClip) return;

    previewVideoRef.current?.pause();
    setIsTimelinePreviewPlaying(false);
    setTimelinePreviewClipKey('');
  }, [timelinePreviewClip, timelinePreviewClipKey]);

  useEffect(() => {
    const video = previewVideoRef.current;
    if (!video || !timelinePreviewClip || isMediaPoolPreview) return;

    if (!isTimelinePreviewPlaying) {
      video.pause();
      return;
    }

    const trimStart = Number(timelinePreviewClip.trimStartSeconds) || 0;
    const trimEnd = Number(timelinePreviewClip.trimEndSeconds)
      || Number(timelinePreviewClip.duration)
      || 0;
    if (video.currentTime < trimStart || video.currentTime >= trimEnd - (1 / 60)) {
      video.currentTime = trimStart;
    }
    video.play().catch(() => setIsTimelinePreviewPlaying(false));
  }, [isMediaPoolPreview, isTimelinePreviewPlaying, timelinePreviewClip]);

  useEffect(() => {
    if (!isCoverPickerOpen && !trimEditor) return undefined;

    function closeOnEscape(event) {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      if (trimEditor) {
        if (trimEditor.selectionStage !== 'start') {
          resetTrimSelection();
        } else {
          closeTrimEditor();
        }
        return;
      }
      setIsCoverPickerOpen(false);
    }

    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [isCoverPickerOpen, trimEditor]);

  useEffect(() => {
    if (!trimEditor) return undefined;

    function toggleTrimPreviewWithSpacebar(event) {
      if (event.code !== 'Space' || event.repeat) return;
      const target = event.target;
      if (
        target instanceof HTMLTextAreaElement
        || target instanceof HTMLSelectElement
        || (target instanceof HTMLInputElement && target.type !== 'range')
        || target?.isContentEditable
      ) {
        return;
      }
      if (!hasPlayableTrimSelection) return;
      event.preventDefault();
      toggleTrimPreview();
    }

    window.addEventListener('keydown', toggleTrimPreviewWithSpacebar);
    return () => window.removeEventListener('keydown', toggleTrimPreviewWithSpacebar);
  }, [hasPlayableTrimSelection, trimEditor, trimPlaybackEndSeconds]);

  useEffect(() => {
    const video = coverPickerVideoRef.current;
    if (!isCoverPickerOpen || !video || !draftCoverPosition || video.readyState < 1) return;
    if (Math.abs(video.currentTime - draftCoverPosition.timeSeconds) > 0.01) {
      video.currentTime = draftCoverPosition.timeSeconds;
    }
  }, [draftCoverPosition, isCoverPickerOpen]);

  useEffect(() => {
    if (!musicTracks.length) {
      if (selectedMusicName) {
        setSelectedMusicName('');
      }
      setShouldAutoplayMusic(false);
      return;
    }

    if (selectedMusicName && !musicTracks.some((track) => track.name === selectedMusicName)) {
      setSelectedMusicName('');
      setShouldAutoplayMusic(false);
    }
  }, [musicTracks, selectedMusicName]);

  useEffect(() => {
    if (!selectedMusic || !shouldAutoplayMusic) return;

    musicAudioRef.current?.play().catch(() => {});
    setShouldAutoplayMusic(false);
  }, [selectedMusic, shouldAutoplayMusic]);

  useEffect(() => {
    function updatePreviewFrameSize() {
      const previewPanel = previewPanelRef.current;
      const panelWidth = previewPanel?.clientWidth || window.innerWidth;
      const contentWidth = Math.max(160, panelWidth - 36);
      const isMobile = window.innerWidth <= 820;
      const formatWidth = selectedFormat?.width || 1080;
      const formatHeight = selectedFormat?.height || 1920;
      const frameAspect = formatHeight / formatWidth;
      const isHorizontal = formatWidth > formatHeight;
      const maxDesignWidth = isMobile ? 420 : isHorizontal ? 680 : 440;
      const panelStyles = previewPanel ? window.getComputedStyle(previewPanel) : null;
      const panelVerticalPadding = panelStyles
        ? parseFloat(panelStyles.paddingTop) + parseFloat(panelStyles.paddingBottom)
        : 32;
      const panelRowGap = panelStyles ? parseFloat(panelStyles.rowGap) : 16;
      const timelineHeight = previewPanel?.querySelector('.timeline-panel')?.offsetHeight || 128;
      const availableHeight = isMobile
        ? window.innerHeight * 0.72
        : Math.max(
            120,
            (previewPanel?.clientHeight || window.innerHeight)
              - panelVerticalPadding
              - panelRowGap
              - timelineHeight,
          );
      const heightBoundWidth = Math.max(120, availableHeight / frameAspect);
      const width = Math.floor(
        Math.min(contentWidth, maxDesignWidth, heightBoundWidth),
      );

      setPreviewFrameSize({
        width,
        height: Math.floor(width * frameAspect),
      });
    }

    updatePreviewFrameSize();
    window.addEventListener('resize', updatePreviewFrameSize);

    let observer;
    if (previewPanelRef.current && 'ResizeObserver' in window) {
      observer = new ResizeObserver(updatePreviewFrameSize);
      observer.observe(previewPanelRef.current);
    }

    return () => {
      window.removeEventListener('resize', updatePreviewFrameSize);
      observer?.disconnect();
    };
  }, [selectedFormat]);

  function openTrimEditorForMedia(clip) {
    cancelTimelinePreview();
    const duration = clipDuration(clip);
    setHoveredMediaClipKey('');
    setError('');
    setTrimPreviewTime(0);
    setTrimHoverTime(0);
    setIsTrimTimelineHovered(false);
    setIsTrimPreviewPlaying(false);
    setActiveTrimBracket('');
    setTrimEditor({
      mode: 'add',
      clip: clipWithDefaultAttributes(clip),
      duration,
      startSeconds: 0,
      endSeconds: duration,
      selectionStage: 'start',
    });
  }

  function openTrimEditorForTimeline(clip) {
    cancelTimelinePreview();
    const duration = Number(clip.duration) || 0;
    const startSeconds = Number(clip.trimStartSeconds) || 0;
    const endSeconds = Number.isFinite(Number(clip.trimEndSeconds))
      ? Number(clip.trimEndSeconds)
      : duration;
    setActiveClipKey(timelineClipId(clip));
    setError('');
    setTrimPreviewTime(startSeconds);
    setTrimHoverTime(startSeconds);
    setIsTrimTimelineHovered(false);
    setIsTrimPreviewPlaying(false);
    setActiveTrimBracket('');
    setTrimEditor({
      mode: 'edit',
      clip,
      duration,
      startSeconds,
      endSeconds,
      selectionStage: 'complete',
    });
  }

  function closeTrimEditor() {
    if (trimScrubAnimationRef.current !== null) {
      window.cancelAnimationFrame(trimScrubAnimationRef.current);
      trimScrubAnimationRef.current = null;
    }
    trimVideoRef.current?.pause();
    setIsTrimPreviewPlaying(false);
    setIsTrimTimelineHovered(false);
    setActiveTrimBracket('');
    setTrimEditor(null);
  }

  function cancelTimelinePreview() {
    previewVideoRef.current?.pause();
    setIsTimelinePreviewPlaying(false);
    setTimelinePreviewClipKey('');
  }

  function toggleTimelinePreview() {
    if (!selectedClips.length) return;

    const video = previewVideoRef.current;
    if (isTimelinePreviewPlaying) {
      video?.pause();
      setIsTimelinePreviewPlaying(false);
      return;
    }

    setHoveredMediaClipKey('');
    if (!timelinePreviewClip) {
      const firstClip = selectedClips[0];
      const firstClipKey = timelineClipId(firstClip);
      setTimelinePreviewClipKey(firstClipKey);
      setActiveClipKey(firstClipKey);
    }
    setIsTimelinePreviewPlaying(true);
  }

  function advanceTimelinePreview(clip) {
    if (!isTimelinePreviewPlaying) return;

    const currentIndex = selectedClips.findIndex(
      (item) => timelineClipId(item) === timelineClipId(clip),
    );
    const nextClip = selectedClips[currentIndex + 1];
    previewVideoRef.current?.pause();

    if (nextClip) {
      const nextClipKey = timelineClipId(nextClip);
      setTimelinePreviewClipKey(nextClipKey);
      setActiveClipKey(nextClipKey);
      return;
    }

    const firstClipKey = timelineClipId(selectedClips[0]);
    setIsTimelinePreviewPlaying(false);
    setTimelinePreviewClipKey('');
    setActiveClipKey(firstClipKey);
  }

  function resetTrimSelection() {
    if (!trimEditor) return;
    if (trimScrubAnimationRef.current !== null) {
      window.cancelAnimationFrame(trimScrubAnimationRef.current);
      trimScrubAnimationRef.current = null;
    }
    const video = trimVideoRef.current;
    if (video) {
      video.pause();
      seekTrimVideoFrame(video, 0);
      video.play().catch(() => {});
    }
    setTrimEditor((current) => current ? {
      ...current,
      startSeconds: 0,
      endSeconds: current.duration,
      selectionStage: 'start',
    } : current);
    setTrimPreviewTime(0);
    setTrimHoverTime(0);
    setIsTrimTimelineHovered(false);
    setIsTrimPreviewPlaying(false);
    setActiveTrimBracket('');
  }

  function handleTrimMetadata(event) {
    if (!trimEditor) return;
    const metadata = videoElementMetadata(event.currentTarget);
    hydrateClipMetadata(sourceClipKey(trimEditor.clip), metadata);
    setTrimEditor((current) => {
      if (!current) return current;
      const wasWaitingForDuration = current.duration <= 0 || current.endSeconds <= 0;
      const nextEnd = wasWaitingForDuration
        ? metadata.duration
        : Math.min(current.endSeconds, metadata.duration);
      return {
        ...current,
        clip: {
          ...current.clip,
          width: metadata.width,
          height: metadata.height,
          aspectRatio: (
            metadata.width && metadata.height
              ? metadata.width / metadata.height
              : current.clip.aspectRatio
          ),
        },
        duration: metadata.duration,
        endSeconds: nextEnd,
      };
    });
    const startSeconds = Number(trimEditor.startSeconds) || 0;
    event.currentTarget.currentTime = startSeconds;
    setTrimPreviewTime(startSeconds);
    if (
      trimEditor.mode === 'add'
      && trimEditor.selectionStage === 'start'
      && !isTrimTimelineHovered
    ) {
      event.currentTarget.play().catch(() => {});
    }
  }

  function updateTrimStart(value) {
    setTrimEditor((current) => {
      if (!current) return current;
      const minimumDuration = Math.min(MIN_TIMELINE_CLIP_SECONDS, current.duration);
      const nextStart = Math.min(
        Math.max(0, Number(value)),
        Math.max(0, current.endSeconds - minimumDuration),
      );
      seekTrimVideoFrame(trimVideoRef.current, nextStart);
      setTrimPreviewTime(nextStart);
      return { ...current, startSeconds: nextStart };
    });
  }

  function updateTrimEnd(value) {
    setTrimEditor((current) => {
      if (!current) return current;
      const minimumDuration = Math.min(MIN_TIMELINE_CLIP_SECONDS, current.duration);
      const nextEnd = Math.max(
        Math.min(current.duration, Number(value)),
        current.startSeconds + minimumDuration,
      );
      const previewTime = Math.max(current.startSeconds, nextEnd - (1 / 30));
      seekTrimVideoFrame(trimVideoRef.current, previewTime);
      setTrimPreviewTime(previewTime);
      return { ...current, endSeconds: nextEnd };
    });
  }

  function updateTrimClipScaleMode(scaleMode) {
    setTrimEditor((current) => current ? {
      ...current,
      clip: clipWithDefaultAttributes({ ...current.clip, scaleMode }),
    } : current);
  }

  function updateTrimClipCropXPercent(event) {
    const cropXPercent = boundedPercent(Number(event.currentTarget.value));
    setTrimEditor((current) => current ? {
      ...current,
      clip: clipWithDefaultAttributes({ ...current.clip, cropXPercent }),
    } : current);
  }

  function nudgeTrimBracket(bracket, direction) {
    setActiveTrimBracket(bracket);
    setTrimEditor((current) => {
      if (!current) return current;
      const minimumDuration = Math.min(MIN_TIMELINE_CLIP_SECONDS, current.duration);

      if (bracket === 'start') {
        const nextStart = Math.min(
          Math.max(0, current.startSeconds + (direction * TIMELINE_FRAME_SECONDS)),
          Math.max(0, current.endSeconds - minimumDuration),
        );
        seekTrimVideoFrame(trimVideoRef.current, nextStart);
        setTrimPreviewTime(nextStart);
        return { ...current, startSeconds: Number(nextStart.toFixed(6)) };
      }

      const nextEnd = Math.max(
        Math.min(current.duration, current.endSeconds + (direction * TIMELINE_FRAME_SECONDS)),
        current.startSeconds + minimumDuration,
      );
      const previewTime = Math.max(current.startSeconds, nextEnd - TIMELINE_FRAME_SECONDS);
      seekTrimVideoFrame(trimVideoRef.current, previewTime);
      setTrimPreviewTime(previewTime);
      return { ...current, endSeconds: Number(nextEnd.toFixed(6)) };
    });
  }

  function handleTrimBracketKeyDown(event, bracket) {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    event.preventDefault();
    event.stopPropagation();
    nudgeTrimBracket(bracket, event.key === 'ArrowRight' ? 1 : -1);
  }

  function trimTimeFromPointer(event) {
    if (!trimEditor?.duration) return 0;
    const bounds = event.currentTarget.getBoundingClientRect();
    const position = Math.min(1, Math.max(0, (event.clientX - bounds.left) / bounds.width));
    return Number((position * trimEditor.duration).toFixed(3));
  }

  function seekTrimVideoFrame(video, timeSeconds) {
    if (!video) return;
    if (typeof video.fastSeek === 'function') {
      video.fastSeek(timeSeconds);
    } else {
      video.currentTime = timeSeconds;
    }
  }

  function applyTrimPreviewTime(timeSeconds) {
    const nextTime = Math.min(
      trimEditor?.duration || 0,
      Math.max(0, Number(timeSeconds)),
    );
    const video = trimVideoRef.current;
    if (video) {
      video.pause();
      seekTrimVideoFrame(video, nextTime);
    }
    setIsTrimPreviewPlaying(false);
    setTrimPreviewTime(nextTime);
    setTrimHoverTime(nextTime);
  }

  function previewTrimTimelineTime(timeSeconds) {
    pendingTrimScrubTimeRef.current = timeSeconds;
    if (trimScrubAnimationRef.current !== null) return;

    trimScrubAnimationRef.current = window.requestAnimationFrame(() => {
      trimScrubAnimationRef.current = null;
      applyTrimPreviewTime(pendingTrimScrubTimeRef.current);
    });
  }

  function previewTrimTimelineTimeImmediately(timeSeconds) {
    if (trimScrubAnimationRef.current !== null) {
      window.cancelAnimationFrame(trimScrubAnimationRef.current);
      trimScrubAnimationRef.current = null;
    }
    pendingTrimScrubTimeRef.current = timeSeconds;
    applyTrimPreviewTime(timeSeconds);
  }

  function handleTrimTimelinePointerMove(event) {
    const pointerTime = trimTimeFromPointer(event);
    const previewTime = isTrimSelectionComplete
      ? Math.min(
          trimEditor?.endSeconds || 0,
          Math.max(trimEditor?.startSeconds || 0, pointerTime),
        )
      : pointerTime;
    previewTrimTimelineTime(previewTime);
  }

  function handleTrimTimelinePointerEnter(event) {
    setIsTrimTimelineHovered(true);
    if (isChoosingTrimStart) {
      previewTrimTimelineTimeImmediately(trimTimeFromPointer(event));
    }
  }

  function markTrimBoundary(timeSeconds) {
    if (!trimEditor || isTrimSelectionComplete) return;

    if (isChoosingTrimStart) {
      const latestStart = Math.max(0, trimEditor.duration - MIN_TIMELINE_CLIP_SECONDS);
      const startSeconds = Math.min(latestStart, Math.max(0, timeSeconds));
      setTrimEditor((current) => current ? {
        ...current,
        startSeconds,
        endSeconds: current.duration,
        selectionStage: 'end',
      } : current);
      previewTrimTimelineTimeImmediately(startSeconds);
      return;
    }

    if (timeSeconds - trimEditor.startSeconds < MIN_TIMELINE_CLIP_SECONDS - 0.001) {
      return;
    }

    const endSeconds = Math.min(trimEditor.duration, timeSeconds);
    setTrimEditor((current) => current ? {
      ...current,
      endSeconds,
      selectionStage: 'complete',
    } : current);
    const previewTime = Math.max(trimEditor.startSeconds, endSeconds - (1 / 30));
    seekTrimVideoFrame(trimVideoRef.current, previewTime);
    setTrimPreviewTime(previewTime);
    setTrimHoverTime(endSeconds);
  }

  function handleTrimTimelineClick(event) {
    const pointerTime = trimTimeFromPointer(event);
    if (isTrimSelectionComplete) {
      previewTrimTimelineTimeImmediately(Math.min(
        trimEditor?.endSeconds || 0,
        Math.max(trimEditor?.startSeconds || 0, pointerTime),
      ));
      return;
    }
    markTrimBoundary(pointerTime);
  }

  function handleTrimPreviewTimeUpdate(event) {
    if (!trimEditor) return;
    const video = event.currentTarget;
    if (!video.paused && video.currentTime >= trimPlaybackEndSeconds - (1 / 60)) {
      video.currentTime = trimEditor.startSeconds;
      setTrimPreviewTime(trimEditor.startSeconds);
      return;
    }
    setTrimPreviewTime(video.currentTime);
  }

  function toggleTrimPreview() {
    const video = trimVideoRef.current;
    if (!video || !trimEditor || !hasPlayableTrimSelection) return;
    if (!video.paused) {
      video.pause();
      setIsTrimPreviewPlaying(false);
      return;
    }
    if (
      video.currentTime < trimEditor.startSeconds
      || video.currentTime >= trimPlaybackEndSeconds - TIMELINE_FRAME_SECONDS - 0.001
    ) {
      video.currentTime = trimEditor.startSeconds;
    }
    video.play().then(() => setIsTrimPreviewPlaying(true)).catch(() => {});
  }

  function saveTrimmedClip() {
    if (!trimEditor) return;
    const isUsingFullClip = trimEditor.selectionStage === 'start';
    if (!isUsingFullClip && trimEditor.selectionStage !== 'complete') {
      return;
    }
    const trimStartSeconds = isUsingFullClip ? 0 : trimEditor.startSeconds;
    const trimEndSeconds = isUsingFullClip ? trimEditor.duration : trimEditor.endSeconds;
    const selectedDuration = trimEndSeconds - trimStartSeconds;
    if (selectedDuration < MIN_TIMELINE_CLIP_SECONDS - 0.001) {
      setError('Choose at least 0.10 seconds of the clip.');
      return;
    }

    const trimAttributes = {
      trimStartSeconds: Number(trimStartSeconds.toFixed(3)),
      trimEndSeconds: Number(trimEndSeconds.toFixed(3)),
      scaleMode: trimEditor.clip.scaleMode || 'fit',
      cropXPercent: boundedPercent(trimEditor.clip.cropXPercent ?? 50),
    };
    if (trimEditor.mode === 'edit') {
      const instanceId = timelineClipId(trimEditor.clip);
      setTimelineClips((current) => current.map((clip) => (
        timelineClipId(clip) === instanceId
          ? { ...clip, ...trimAttributes }
          : clip
      )));
      if (coverFrame?.clipKey === instanceId) {
        setCoverFrame(null);
        setCoverOutput(null);
      }
      setActiveClipKey(instanceId);
    } else {
      const instanceId = createTimelineClipId();
      const timelineClip = {
        ...clipWithDefaultAttributes(trimEditor.clip),
        ...trimAttributes,
        sourceKey: sourceClipKey(trimEditor.clip),
        instanceId,
      };
      setTimelineClips((current) => [...current, timelineClip]);
      setActiveClipKey(instanceId);
    }
    setJob(null);
    closeTrimEditor();
  }

  function removeClipFromTimeline(event, clipKey) {
    event.stopPropagation();
    event.preventDefault();

    setTimelineClips((current) => {
      const removedIndex = current.findIndex((clip) => timelineClipId(clip) === clipKey);
      const next = current.filter((clip) => timelineClipId(clip) !== clipKey);

      if (activeClipKey === clipKey) {
        const fallbackClip = next[removedIndex] || next[removedIndex - 1] || null;
        setActiveClipKey(fallbackClip?.key || '');
      }

      return next;
    });
    setDraggedClipKey('');
    setDragOverClipKey('');
  }

  function moveClipInTimeline(sourceKey, targetKey) {
    if (!sourceKey || !targetKey || sourceKey === targetKey) return;

    setTimelineClips((current) => {
      const sourceIndex = current.findIndex((clip) => timelineClipId(clip) === sourceKey);
      const targetIndex = current.findIndex((clip) => timelineClipId(clip) === targetKey);
      if (sourceIndex < 0 || targetIndex < 0) return current;

      const next = [...current];
      const [movedClip] = next.splice(sourceIndex, 1);
      next.splice(targetIndex, 0, movedClip);
      return next;
    });
  }

  function handleTimelineDragStart(event, clipKey) {
    setDraggedClipKey(clipKey);
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', clipKey);
  }

  function handleTimelineDrop(event, targetKey) {
    event.preventDefault();
    const sourceKey = event.dataTransfer.getData('text/plain') || draggedClipKey;
    moveClipInTimeline(sourceKey, targetKey);
    setDraggedClipKey('');
    setDragOverClipKey('');
  }

  function chooseFormat(option) {
    setSelectedFormatOption(option);
    setJob(null);
    setCoverOutput(null);
  }

  function chooseMediaFolder(folderPath) {
    setSelectedFolderPath(folderPath);
    setJob(null);
  }

  function updateText(value) {
    setText(value);
    setError('');
  }

  function updateTextYPercent(event) {
    setTextYPercent(Number(event.currentTarget.value));
  }

  function updateTextHorizontalPadding(event) {
    setTextHorizontalPadding(
      boundedTextHorizontalPadding(Number(event.currentTarget.value), selectedFormat),
    );
  }

  function toggleAddCta(event) {
    setAddCta(event.currentTarget.checked);
    setJob(null);
  }

  function updateCtaText(value) {
    setCtaText(value);
    setJob(null);
  }

  function chooseMusic(musicName, shouldAutoplay = false) {
    const isDeselecting = selectedMusicName === musicName;
    if (isDeselecting) musicAudioRef.current?.pause();
    setShouldAutoplayMusic(shouldAutoplay && !isDeselecting);
    setSelectedMusicName(isDeselecting ? '' : musicName);
    setJob(null);
  }

  function updateMusicLibrary(payload) {
    setBootstrap((current) => ({
      ...(current || {}),
      music: payload.music || current?.music || [],
    }));
  }

  async function uploadMusicFile(event) {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = '';
    if (!file) return;

    setError('');
    setIsUploadingMusic(true);
    try {
      const payload = await api.upload(
        `/api/music/upload?filename=${encodeURIComponent(file.name)}`,
        file,
      );
      updateMusicLibrary(payload);
      if (payload.track?.name) {
        setSelectedMusicName(payload.track.name);
        setShouldAutoplayMusic(false);
      }
      setJob(null);
    } catch (caught) {
      setError(caught.message);
    } finally {
      setIsUploadingMusic(false);
    }
  }

  async function deleteMusic(trackName) {
    if (!window.confirm(`Remove "${trackName}" from the music library?`)) {
      return;
    }

    setError('');
    setDeletingMusicName(trackName);
    try {
      const payload = await api.post('/api/music/delete', { music_name: trackName });
      updateMusicLibrary(payload);
      if (selectedMusicName === trackName) {
        setSelectedMusicName('');
        setShouldAutoplayMusic(false);
      }
      setJob(null);
    } catch (caught) {
      setError(caught.message);
    } finally {
      setDeletingMusicName('');
    }
  }

  async function importMusicFromUrl() {
    const url = musicImportUrl.trim();
    if (!url) {
      setError('Enter an Instagram video URL.');
      return;
    }

    setError('');
    setIsImportingMusic(true);
    try {
      const payload = await api.post('/api/music/import', {
        url,
        use_browser_cookies: useInstagramLogin,
        browser: 'chrome',
      });
      updateMusicLibrary(payload);
      if (payload.track?.name) {
        setSelectedMusicName(payload.track.name);
        setShouldAutoplayMusic(false);
      }
      setMusicImportUrl('');
      setJob(null);
    } catch (caught) {
      setError(caught.message);
    } finally {
      setIsImportingMusic(false);
    }
  }

  function openCoverPicker() {
    if (!coverTimeline.length) {
      setError('Wait for the selected clips to finish loading before choosing a cover image.');
      return;
    }

    setError('');
    setCoverPickerOffset(coverOffsetForFrame(coverTimeline, coverFrame));
    setCoverCropXPercent(boundedPercent(coverFrame?.cropXPercent ?? 50));
    setIsCoverPickerOpen(true);
  }

  function saveCoverFrame() {
    if (!draftCoverPosition) return;
    setCoverFrame({
      clipKey: timelineClipId(draftCoverPosition.clip),
      timeSeconds: Number(draftCoverPosition.timeSeconds.toFixed(3)),
      cropXPercent: boundedPercent(coverCropXPercent),
    });
    setCoverOutput(null);
    setIsCoverPickerOpen(false);
  }

  async function renderCoverImage() {
    if (!coverFrame || !selectedCoverClip) {
      setError('Choose a frame before rendering the cover image.');
      return;
    }

    setError('');
    setIsRenderingCover(true);
    try {
      const payload = await api.post('/api/render-cover', {
        clip: {
          folder_path: selectedCoverClip.folderPath,
          clip_name: selectedCoverClip.name,
          crop_x_percent: boundedPercent(coverFrame.cropXPercent ?? 50),
          trim_start_seconds: selectedCoverClip.trimStartSeconds || 0,
          trim_end_seconds: selectedCoverClip.trimEndSeconds,
        },
        time_seconds: coverFrame.timeSeconds,
      });
      setCoverOutput(payload.cover);
    } catch (caught) {
      setError(caught.message);
    } finally {
      setIsRenderingCover(false);
    }
  }

  async function startRender() {
    if (!selectedFormatOption || selectedClips.length === 0) {
      setError('Choose a format and at least one clip before rendering.');
      return;
    }
    setError('');
    setSelectedSettingsTab('render');
    setSelectedRenderSubTab('video');
    const payload = await api.post('/api/render', {
      format_option: selectedFormatOption,
      clips: selectedClips.map((clip) => ({
        folder_path: clip.folderPath,
        clip_name: clip.name,
        scale_mode: clip.scaleMode || 'fit',
        crop_x_percent: boundedPercent(clip.cropXPercent ?? 50),
        trim_start_seconds: clip.trimStartSeconds || 0,
        trim_end_seconds: clip.trimEndSeconds,
      })),
      text: text.trim(),
      font_size: fontSize,
      text_y_percent: textYPercent,
      text_horizontal_padding: boundedTextHorizontalPadding(
        textHorizontalPadding,
        selectedFormat,
      ),
      add_cta: addCta,
      cta_text: ctaText.trim(),
      music_name: selectedMusicName || null,
    });
    setJob(payload.job);
  }

  const isRendering = job && ['queued', 'running'].includes(job.status);
  const canUseText = true;
  const canUseMusic = true;
  const canRender = Boolean(selectedFormatOption && selectedClips.length > 0);

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          {onHome ? (
            <button
              className="back-home-button"
              onClick={onHome}
              type="button"
            >
              <ArrowLeft size={16} />
              Home
            </button>
          ) : null}
          <Film size={24} strokeWidth={2.2} />
          <h1>Short Form Video Creator</h1>
        </div>
        <div className="topbar-metrics" aria-label="current selection">
          <span>{selectedClips.length} clips</span>
          <span>{formatSeconds(estimatedOutputSeconds)}</span>
          <span>{selectedFormat ? `${selectedFormat.width}x${selectedFormat.height}` : 'No format'}</span>
        </div>
      </header>

      <main className="creator-grid">
        <aside className="settings-sidebar" aria-label="creator settings">
          <nav className="settings-tabs" aria-label="creator settings tabs" role="tablist">
            {settingsTabs.map((tab) => (
              <button
                aria-selected={selectedSettingsTab === tab.id}
                className={classNames(
                  'settings-tab',
                  selectedSettingsTab === tab.id && 'is-active',
                )}
                key={tab.id}
                onClick={() => setSelectedSettingsTab(tab.id)}
                role="tab"
                type="button"
              >
                {tab.icon}
                <span>{tab.label}</span>
              </button>
            ))}
          </nav>

          <section
            className={classNames(
              'settings-content',
              selectedSettingsTab === 'music' && 'is-music-content',
            )}
            aria-label={`${selectedSettingsTab} settings`}
          >
            {error ? <div className="error-banner">{error}</div> : null}
            {selectedSettingsTab === 'format' ? (
              <div className="tab-pane">
                <div className="section-title">
                  <h2>Format</h2>
                  <span>{selectedFormat ? selectedFormat.aspectRatio : 'Choose'}</span>
                </div>
              <div className="format-list">
                {formats.map((format) => (
                  <button
                    className={classNames(
                      'format-button',
                      selectedFormatOption === format.option && 'is-active',
                    )}
                    key={format.option}
                    onClick={() => chooseFormat(format.option)}
                    type="button"
                  >
                    <span>{format.aspectRatio || format.title}</span>
                  </button>
                ))}
              </div>
              </div>
            ) : null}

            {selectedSettingsTab === 'text' ? (
              <div className="tab-pane">
                <div className="section-title">
                  <h2>Text</h2>
                  <span>{text.trim() ? 'Active' : 'Optional'}</span>
                </div>
              <textarea
                className="caption-input"
                disabled={!canUseText}
                placeholder="every ordinary second was golden"
                value={text}
                onChange={(event) => updateText(event.target.value)}
                rows={4}
              />
              <div className="text-size-row">
                {bootstrap?.textSizes?.map((option) => (
                  <button
                    className={classNames(
                      'size-button',
                      fontSize === option.fontSize && 'is-active',
                    )}
                    key={option.option}
                    disabled={!canUseText}
                    onClick={() => setFontSize(option.fontSize)}
                    type="button"
                  >
                    {option.label}
                  </button>
                ))}
              </div>
              <label className="slider-row">
                <span>Vertical</span>
                <input
                  disabled={!canUseText}
                  max="90"
                  min="10"
                  onChange={updateTextYPercent}
                  onInput={updateTextYPercent}
                  type="range"
                  value={textYPercent}
                />
                <span>{Math.round(textYPercent)}%</span>
              </label>
              <label className="slider-row text-padding-row">
                <span>Padding</span>
                <input
                  disabled={!canUseText}
                  max={maxTextHorizontalPadding(selectedFormat)}
                  min="0"
                  onChange={updateTextHorizontalPadding}
                  onInput={updateTextHorizontalPadding}
                  type="range"
                  value={boundedTextHorizontalPadding(textHorizontalPadding, selectedFormat)}
                />
                <span>
                  {Math.round(boundedTextHorizontalPadding(
                    textHorizontalPadding,
                    selectedFormat,
                  ))}px
                </span>
              </label>
              </div>
            ) : null}

            {selectedSettingsTab === 'music' ? (
              <div className="tab-pane music-tab-pane">
                <div className="section-title">
                  <h2>Music</h2>
                  <span>{selectedMusicName ? 'Selected' : 'Optional'}</span>
                </div>
                <div className="music-manager">
                  <div className="music-actions">
                    <label className="file-button">
                      {isUploadingMusic ? <Loader2 className="spin" size={16} /> : <Upload size={16} />}
                      <span>{isUploadingMusic ? 'Uploading' : 'Add Song'}</span>
                      <input
                        accept="audio/*,.mp3,.m4a,.wav,.aac,.mov,.mp4"
                        disabled={isUploadingMusic}
                        onChange={uploadMusicFile}
                        type="file"
                      />
                    </label>
                  </div>

                  <div className="music-import-controls">
                    <div className="music-import-row">
                      <input
                        className="url-input"
                        disabled={isImportingMusic}
                        onChange={(event) => setMusicImportUrl(event.target.value)}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter') {
                            event.preventDefault();
                            importMusicFromUrl();
                          }
                        }}
                        placeholder="Instagram video URL"
                        type="url"
                        value={musicImportUrl}
                      />
                      <button
                        className="secondary-button"
                        disabled={isImportingMusic || !musicImportUrl.trim()}
                        onClick={importMusicFromUrl}
                        type="button"
                      >
                        {isImportingMusic ? <Loader2 className="spin" size={16} /> : <Download size={16} />}
                        Import MP3
                      </button>
                    </div>
                    <label className="instagram-login-option">
                      <input
                        checked={useInstagramLogin}
                        disabled={isImportingMusic}
                        onChange={(event) => setUseInstagramLogin(event.target.checked)}
                        type="checkbox"
                      />
                      <span>Use Chrome login for restricted posts</span>
                    </label>
                  </div>

                  <div className="music-library-header">
                    <span>Library</span>
                    <strong>{musicTracks.length} songs</strong>
                  </div>
                  <div className="music-track-list" aria-label="music library">
                    {musicTracks.map((track) => (
                      <div
                        className={classNames(
                          'music-track-card',
                          selectedMusicName === track.name && 'is-active',
                        )}
                        key={track.name}
                      >
                        <button
                          aria-pressed={selectedMusicName === track.name}
                          className="music-track-select"
                          disabled={!canUseMusic}
                          onClick={() => chooseMusic(track.name, true)}
                          type="button"
                        >
                          <span className="music-track-main">
                            <strong>{track.name}</strong>
                            <span>{formatSeconds(track.duration)}</span>
                          </span>
                        </button>
                        {selectedMusicName === track.name ? (
                          <Check className="music-track-check" size={15} />
                        ) : null}
                        <button
                          aria-label={`Remove ${track.name}`}
                          className="music-delete-button"
                          disabled={deletingMusicName === track.name}
                          onClick={() => deleteMusic(track.name)}
                          type="button"
                        >
                          {deletingMusicName === track.name ? (
                            <Loader2 className="spin" size={14} />
                          ) : (
                            <X size={14} />
                          )}
                        </button>
                      </div>
                    ))}

                    {!musicTracks.length ? (
                      <div className="music-empty">Add a song to build your library.</div>
                    ) : null}
                  </div>

                  <div className="music-player">
                    <div className="music-player-meta">
                      <span>Now Playing</span>
                      <strong title={selectedMusic?.name || 'No song selected'}>
                        {selectedMusic?.name || 'No song selected'}
                      </strong>
                    </div>
                    {selectedMusic ? (
                      <audio
                        autoPlay={shouldAutoplayMusic}
                        className="audio-preview"
                        controls
                        key={selectedMusic.name}
                        ref={musicAudioRef}
                        src={selectedMusic.url}
                      />
                    ) : (
                      <div className="audio-placeholder">Select a song to preview it.</div>
                    )}
                  </div>
                </div>
              </div>
            ) : null}

            {selectedSettingsTab === 'render' ? (
              <div className="tab-pane">
                <div className="section-title">
                  <h2>Render</h2>
                  <span>
                    {selectedRenderSubTab === 'cover'
                      ? (isRenderingCover ? 'Rendering' : coverOutput ? 'Complete' : 'Ready')
                      : (job?.status || 'Ready')}
                  </span>
                </div>
                <div className="render-subtabs" role="tablist" aria-label="render output type">
                  <button
                    aria-selected={selectedRenderSubTab === 'video'}
                    className={classNames(
                      'render-subtab',
                      selectedRenderSubTab === 'video' && 'is-active',
                    )}
                    onClick={() => setSelectedRenderSubTab('video')}
                    role="tab"
                    type="button"
                  >
                    <Film size={15} />
                    Video
                  </button>
                  <button
                    aria-selected={selectedRenderSubTab === 'cover'}
                    className={classNames(
                      'render-subtab',
                      selectedRenderSubTab === 'cover' && 'is-active',
                    )}
                    onClick={() => setSelectedRenderSubTab('cover')}
                    role="tab"
                    type="button"
                  >
                    <ImageIcon size={15} />
                    Cover Image
                    {coverOutput ? <Check className="render-subtab-check" size={13} /> : null}
                  </button>
                </div>

                {selectedRenderSubTab === 'video' ? (
                  <div className="render-subpane" role="tabpanel" aria-label="video render settings">
                    <div className="render-summary">
                      <div>
                        <span>Format</span>
                        <strong>{selectedFormat ? selectedFormat.aspectRatio : '9:16'}</strong>
                      </div>
                      <div>
                        <span>Timeline</span>
                        <strong>{selectedClips.length} clips</strong>
                      </div>
                      <div>
                        <span>Length</span>
                        <strong>{formatSeconds(estimatedOutputSeconds)}</strong>
                      </div>
                      <div>
                        <span>Music</span>
                        <strong>{selectedMusicName ? 'Selected' : 'No song'}</strong>
                      </div>
                    </div>
                    {clipsExceedSong ? (
                      <div className="duration-warning" role="alert">
                        <AlertTriangle size={17} />
                        <span>
                          The clips are {formatSeconds(durationDifferenceSeconds)} longer than the song.
                          The video will stop when the song ends, so the final clips will be cut short.
                          You can still generate it.
                        </span>
                      </div>
                    ) : null}
                    <label className="cta-option">
                      <input
                        checked={addCta}
                        disabled={isRendering}
                        onChange={toggleAddCta}
                        type="checkbox"
                      />
                      <span className="cta-checkbox" aria-hidden="true">
                        {addCta ? <Check size={13} strokeWidth={3} /> : null}
                      </span>
                      <span className="cta-option-copy">
                        <strong>CTA</strong>
                        <span>Last 2s</span>
                      </span>
                    </label>
                    {addCta ? (
                      <label className="cta-text-field">
                        <span>CTA text</span>
                        <textarea
                          className="caption-input cta-text-input"
                          disabled={isRendering}
                          maxLength={500}
                          onChange={(event) => updateCtaText(event.target.value)}
                          rows={3}
                          value={ctaText}
                        />
                      </label>
                    ) : null}
                    <button
                      className="primary-button"
                      disabled={isRendering || selectedClips.length === 0 || !canRender}
                      onClick={startRender}
                      type="button"
                    >
                      {isRendering ? <Loader2 className="spin" size={18} /> : <Wand2 size={18} />}
                      {clipsExceedSong ? 'Generate Anyway' : 'Generate Video'}
                    </button>
                    {job ? (
                      <div className="job-panel">
                        <div className="job-header">
                          <span className={classNames('status-dot', job.status)} />
                          <strong>{job.status}</strong>
                        </div>
                        <div className="job-log">
                          {job.logs?.slice(-7).map((line, index) => (
                            <p key={`${line}-${index}`}>{line}</p>
                          ))}
                        </div>
                        {job.outputUrl ? (
                          <a className="output-link" href={job.outputUrl} download>
                            <Download size={16} />
                            Download Video
                          </a>
                        ) : null}
                      </div>
                    ) : null}
                  </div>
                ) : (
                  <div className="render-subpane" role="tabpanel" aria-label="cover image settings">
                    <div className="cover-tab-intro">
                      <ImageIcon size={19} />
                      <div>
                        <strong>
                          {coverOutput
                            ? 'Cover image rendered'
                            : coverFrame ? 'Frame ready to render' : 'Choose a cover frame'}
                        </strong>
                        <span>Always renders as a 1080 × 1920 vertical fill JPG.</span>
                      </div>
                    </div>
                    <button
                      className={classNames('secondary-button', coverFrame && 'is-active')}
                      disabled={isRenderingCover || selectedClips.length === 0}
                      onClick={openCoverPicker}
                      type="button"
                    >
                      <ImageIcon size={18} />
                      {coverFrame ? 'Change Cover Image' : 'Choose Cover Image'}
                    </button>
                    {coverFrame && selectedCoverClip ? (
                      <div className="cover-selection-summary">
                        <span>Chosen frame</span>
                        <strong>{selectedCoverClip.name}</strong>
                        <span>
                          {formatSeconds(coverFrame.timeSeconds)} · X&nbsp;
                          {Math.round(boundedPercent(coverFrame.cropXPercent ?? 50))}%
                        </span>
                      </div>
                    ) : null}
                    <button
                      className="primary-button"
                      disabled={isRenderingCover || !coverFrame || !selectedCoverClip}
                      onClick={renderCoverImage}
                      type="button"
                    >
                      {isRenderingCover ? (
                        <Loader2 className="spin" size={18} />
                      ) : (
                        <ImageIcon size={18} />
                      )}
                      Render Cover Image
                    </button>
                    {coverOutput?.url ? (
                      <a
                        className="output-link cover-output-link"
                        href={coverOutput.url}
                        download
                      >
                        <ImageIcon size={16} />
                        Download Cover JPG
                      </a>
                    ) : null}
                  </div>
                )}
              </div>
            ) : null}
          </section>
        </aside>

        <section className="preview-panel" aria-label="composition preview" ref={previewPanelRef}>
          <div
            className="preview-frame"
            style={{
              width: `${previewFrameSize.width}px`,
              height: `${previewFrameSize.height}px`,
            }}
          >
            {centerPreviewClip ? (
              <div
                className={classNames(
                  'preview-video-box',
                  centerPreviewClip.scaleMode === 'square' && 'is-square',
                  centerPreviewClip.scaleMode === 'fill' && 'is-fill',
                )}
                style={previewVideoBoxStyle(centerPreviewClip, previewFrameSize)}
              >
                <video
                  aria-label={isMediaPoolPreview
                    ? `Preview of ${centerPreviewClip.name}`
                    : undefined}
                  className="preview-video"
                  key={`${isMediaPoolPreview ? 'media' : 'timeline'}-${timelineClipId(centerPreviewClip)}-${centerPreviewClip.scaleMode}-${centerPreviewClip.trimStartSeconds}-${centerPreviewClip.trimEndSeconds}`}
                  src={centerPreviewClip.url}
                  muted
                  autoPlay={isMediaPoolPreview || isTimelinePreviewPlaying}
                  loop={isMediaPoolPreview}
                  onEnded={() => {
                    if (timelinePreviewClip) advanceTimelinePreview(timelinePreviewClip);
                  }}
                  onLoadedMetadata={(event) => {
                    hydrateClipMetadata(
                      sourceClipKey(centerPreviewClip),
                      videoElementMetadata(event.currentTarget),
                    );
                    event.currentTarget.currentTime = isMediaPoolPreview
                      ? 0
                      : centerPreviewClip.trimStartSeconds || 0;
                  }}
                  onTimeUpdate={(event) => {
                    if (
                      isMediaPoolPreview
                      || !isTimelinePreviewPlaying
                      || !timelinePreviewClip
                    ) return;
                    const trimStart = centerPreviewClip.trimStartSeconds || 0;
                    const trimEnd = centerPreviewClip.trimEndSeconds
                      ?? centerPreviewClip.duration;
                    if (event.currentTarget.currentTime >= trimEnd - (1 / 60)) {
                      advanceTimelinePreview(timelinePreviewClip);
                    }
                  }}
                  playsInline
                  preload="auto"
                  ref={previewVideoRef}
                  style={previewVideoStyle(centerPreviewClip)}
                />
              </div>
            ) : null}
            {!isMediaPoolPreview ? (
              addCta ? (
                <div className="preview-cta" aria-hidden="true">
                  <img
                    alt=""
                    className="preview-cta-image"
                    src="/api/media/appstore"
                    style={previewCtaLayout(selectedFormat, previewFrameSize).image}
                  />
                  <p
                    className="preview-cta-text"
                    style={previewCtaLayout(selectedFormat, previewFrameSize).text}
                  >
                    {ctaText}
                  </p>
                </div>
              ) : text.trim() ? (
                <div
                  className="preview-text"
                  style={previewTextStyle(
                    selectedFormat,
                    previewFrameSize,
                    fontSize,
                    textYPercent,
                    textHorizontalPadding,
                  )}
                >
                  {text}
                </div>
              ) : null
            ) : null}
          </div>
          <div className="timeline-panel">
            <div className="timeline-header">
              <h2>Timeline</h2>
              <div className="timeline-header-actions">
                <button
                  aria-label={isTimelinePreviewPlaying
                    ? 'Pause timeline preview'
                    : 'Play timeline preview'}
                  aria-pressed={isTimelinePreviewPlaying}
                  className="timeline-preview-button"
                  disabled={!selectedClips.length}
                  onClick={toggleTimelinePreview}
                  title={isTimelinePreviewPlaying
                    ? 'Pause timeline preview'
                    : 'Play timeline preview'}
                  type="button"
                >
                  {isTimelinePreviewPlaying
                    ? <Pause size={14} />
                    : <Play fill="currentColor" size={14} />}
                </button>
                <span className={clipsExceedSong ? 'is-overrun' : ''}>
                  {selectedClips.length} clips • {formatSeconds(totalSelectedClipSeconds)}
                  {selectedMusicSeconds ? ` / ${formatSeconds(selectedMusicSeconds)}` : ''}
                </span>
              </div>
            </div>
            <div className="timeline-scroll">
              <div
                className="timeline-duration-canvas"
                style={{ width: `${timelineCanvasPixels}px` }}
              >
                <div className="timeline-track" aria-label="selected clip order">
                  {selectedClips.length ? (
                    selectedClips.map((clip, index) => {
                      const instanceId = timelineClipId(clip);
                      return (
                        <div
                          className={classNames(
                            'timeline-card',
                            activeClipKey === instanceId && 'is-active',
                            draggedClipKey === instanceId && 'is-dragging',
                            dragOverClipKey === instanceId
                              && draggedClipKey !== instanceId
                              && 'is-drop-target',
                          )}
                          draggable
                          key={instanceId}
                          onClick={() => openTrimEditorForTimeline(clip)}
                          onKeyDown={(event) => {
                            if (event.key === 'Enter' || event.key === ' ') {
                              event.preventDefault();
                              openTrimEditorForTimeline(clip);
                            }
                          }}
                          onDragEnd={() => {
                            setDraggedClipKey('');
                            setDragOverClipKey('');
                          }}
                          onDragEnter={() => setDragOverClipKey(instanceId)}
                          onDragOver={(event) => event.preventDefault()}
                          onDragStart={(event) => handleTimelineDragStart(event, instanceId)}
                          onDrop={(event) => handleTimelineDrop(event, instanceId)}
                          role="button"
                          style={{
                            flexBasis: `${(clipDuration(clip) / timelineScaleSeconds) * 100}%`,
                          }}
                          tabIndex={0}
                          title={`${clip.name}: ${formatSeconds(clip.trimStartSeconds || 0)} – ${formatSeconds(clip.trimEndSeconds ?? clip.duration)}`}
                        >
                          <video
                            src={clip.url}
                            muted
                            onLoadedMetadata={(event) => {
                              hydrateClipMetadata(
                                sourceClipKey(clip),
                                videoElementMetadata(event.currentTarget),
                              );
                              event.currentTarget.currentTime = clip.trimStartSeconds || 0;
                            }}
                            playsInline
                            preload="metadata"
                          />
                          <button
                            aria-label={`Remove ${clip.name}`}
                            className="timeline-remove"
                            onClick={(event) => removeClipFromTimeline(event, instanceId)}
                            onDragStart={(event) => event.preventDefault()}
                            onMouseDown={(event) => {
                              event.stopPropagation();
                              event.preventDefault();
                            }}
                            type="button"
                          >
                            <X size={14} />
                          </button>
                          <span className="timeline-index">{index + 1}</span>
                          <span className="timeline-name">{clip.name}</span>
                          <span className="timeline-duration">
                            {formatSeconds(clipDuration(clip))}
                          </span>
                        </div>
                      );
                    })
                  ) : (
                    <div className="timeline-empty">Choose a clip from the media pool</div>
                  )}
                </div>
                {selectedMusicSeconds ? (
                  <div className="song-track" aria-label="song duration">
                    <div
                      className={classNames('song-duration-bar', clipsExceedSong && 'is-overrun')}
                      style={{ width: `${songWidthPercent}%` }}
                      title={`${selectedMusic?.name} • ${formatSeconds(selectedMusicSeconds)}`}
                    >
                      <Music2 size={13} />
                      <span>{selectedMusic?.name}</span>
                      <strong>{formatSeconds(selectedMusicSeconds)}</strong>
                    </div>
                  </div>
                ) : null}
              </div>
            </div>
          </div>
        </section>

        <section className="clip-panel" aria-label="media pool">
          <div className="section-title">
            <h2>Media Pool</h2>
            <span>
              {isLoadingMedia
                ? 'Loading'
                : `${folders.length} folders • ${clips.length} clips`}
            </span>
          </div>
          <nav className="media-pathbar" aria-label="media path">
            {(mediaPool?.breadcrumbs || [{ name: 'Media Pool', path: '' }]).map(
              (crumb, index, allCrumbs) => (
                <span className="path-segment" key={crumb.path || 'root'}>
                  <button
                    className={classNames(
                      'path-button',
                      crumb.path === selectedFolderPath && 'is-active',
                    )}
                    onClick={() => chooseMediaFolder(crumb.path)}
                    type="button"
                  >
                    {crumb.name}
                  </button>
                  {index < allCrumbs.length - 1 ? (
                    <ChevronRight size={14} />
                  ) : null}
                </span>
              ),
            )}
          </nav>
          <div className="media-browser">
            {isLoadingMedia
              ? Array.from({ length: 9 }, (_, index) => (
                <div className="media-loading-item" key={`media-loading-${index}`}>
                  <span className="media-loading-icon">
                    <Loader2 className="spin" size={21} />
                  </span>
                  <span className="media-loading-label" />
                </div>
              ))
              : null}

            {folders.length
              ? (
              folders.map((folder) => (
                <button
                  className="media-icon-item folder-icon-item"
                  key={folder.path}
                  onClick={() => chooseMediaFolder(folder.path)}
                  type="button"
                >
                  <span className="media-folder-icon">
                    <Folder size={76} strokeWidth={1.65} />
                  </span>
                  <span className="media-icon-label">
                    {folder.name}
                  </span>
                </button>
              ))
              )
              : null}

            {clips.length
              ? (
              clips.map((clip) => {
                const instanceCount = selectedClips.filter(
                  (item) => sourceClipKey(item) === clip.key,
                ).length;
                return (
                  <button
                    aria-label={`Add a trimmed section of ${clip.name}`}
                    className={classNames(
                      'media-icon-item clip-icon-item',
                      instanceCount > 0 && 'is-selected',
                    )}
                    key={clip.key}
                    onClick={() => openTrimEditorForMedia(clip)}
                    onPointerEnter={() => setHoveredMediaClipKey(clip.key)}
                    onPointerLeave={() => setHoveredMediaClipKey((current) => (
                      current === clip.key ? '' : current
                    ))}
                    type="button"
                  >
                    <span className="media-file-stage">
                      <MediaClipThumbnail
                        clip={clip}
                        onMetadata={hydrateClipMetadata}
                      />
                      <span className="media-action">
                        <Plus size={14} />
                      </span>
                      {instanceCount > 0 ? (
                        <span className="media-order">{instanceCount}×</span>
                      ) : null}
                    </span>
                    <span className="media-icon-label">
                      {clip.name}
                    </span>
                  </button>
                );
              })
              )
              : null}

            {!folders.length && !clips.length && !isLoadingMedia ? (
              <div className="media-empty">
                Empty folder
              </div>
            ) : null}
          </div>
        </section>
      </main>
      {trimEditor ? (
        <div
          className="trim-editor-backdrop"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) closeTrimEditor();
          }}
          role="presentation"
        >
          <section
            aria-labelledby="trim-editor-title"
            aria-modal="true"
            className="trim-editor-dialog"
            role="dialog"
          >
            <header className="trim-editor-header">
              <h2 id="trim-editor-title">Add to timeline</h2>
              <button
                aria-label="Close clip trimmer"
                className="icon-button"
                onClick={closeTrimEditor}
                type="button"
              >
                <X size={18} />
              </button>
            </header>

            <div className="trim-editor-preview">
              <div
                className="trim-editor-preview-frame"
                style={{ aspectRatio: trimPreviewAspectRatio(trimEditor.clip, selectedFormat) }}
              >
                <video
                  autoPlay={trimEditor.mode === 'add' && isChoosingTrimStart}
                  key={`${sourceClipKey(trimEditor.clip)}-${timelineClipId(trimEditor.clip)}`}
                  muted
                  onEnded={() => setIsTrimPreviewPlaying(false)}
                  onLoadedMetadata={handleTrimMetadata}
                  onPause={() => setIsTrimPreviewPlaying(false)}
                  onPlay={() => setIsTrimPreviewPlaying(true)}
                  onTimeUpdate={handleTrimPreviewTimeUpdate}
                  playsInline
                  preload="auto"
                  ref={trimVideoRef}
                  src={trimEditor.clip.url}
                  style={previewVideoStyle(trimEditor.clip)}
                />
              </div>
            </div>

            <div className="trim-editor-options">
              <div className="attribute-group">
                <span className="attribute-label">Scaling</span>
                <div className="scale-mode-list" role="group" aria-label="clip scaling">
                  {CLIP_SCALE_OPTIONS.map((option) => (
                    <button
                      aria-pressed={(trimEditor.clip.scaleMode || 'fit') === option.value}
                      className={classNames(
                        'scale-mode-button',
                        (trimEditor.clip.scaleMode || 'fit') === option.value && 'is-active',
                      )}
                      key={option.value}
                      onClick={() => updateTrimClipScaleMode(option.value)}
                      type="button"
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
              </div>
              {clipUsesHorizontalCrop(trimEditor.clip) ? (
                <label className="slider-row clip-axis-row">
                  <span>X Axis</span>
                  <input
                    aria-label="Clip X axis"
                    max="100"
                    min="0"
                    onChange={updateTrimClipCropXPercent}
                    onInput={updateTrimClipCropXPercent}
                    type="range"
                    value={boundedPercent(trimEditor.clip.cropXPercent ?? 50)}
                  />
                  <span>
                    {Math.round(boundedPercent(trimEditor.clip.cropXPercent ?? 50))}%
                  </span>
                </label>
              ) : null}
            </div>

            <div className="trim-editor-selection">
              <div aria-live="polite" className="trim-clip-length">
                {displayedTrimDuration === null
                  ? null
                  : `Clip length ${formatSeconds(displayedTrimDuration)}`}
              </div>
              <div className="trim-selection-controls">
                <button
                  aria-label={isTrimPreviewPlaying
                    ? 'Pause selection preview'
                    : 'Play selection preview'}
                  className="trim-play-button"
                  disabled={!hasPlayableTrimSelection}
                  onClick={toggleTrimPreview}
                  type="button"
                >
                  {isTrimPreviewPlaying ? <Pause size={16} /> : <Play size={16} />}
                </button>
                <div
                  aria-label={isChoosingTrimStart
                    ? 'Choose trim start on clip timeline'
                    : isChoosingTrimEnd
                      ? 'Choose trim end on clip timeline'
                      : 'Clip trim timeline'}
                  className={classNames(
                    'trim-bracket-timeline',
                    !isTrimSelectionComplete && 'is-marking',
                  )}
                  onClick={handleTrimTimelineClick}
                  onKeyDown={(event) => {
                    if (
                      !isTrimSelectionComplete
                      && event.key === 'Enter'
                    ) {
                      event.preventDefault();
                      markTrimBoundary(trimHoverTime);
                    }
                  }}
                  onPointerEnter={handleTrimTimelinePointerEnter}
                  onPointerLeave={() => setIsTrimTimelineHovered(false)}
                  onPointerMove={handleTrimTimelinePointerMove}
                  role={!isTrimSelectionComplete ? 'button' : undefined}
                  tabIndex={!isTrimSelectionComplete ? 0 : -1}
                >
                  <div
                    className="trim-filmstrip"
                    style={trimEditorThumbnailUrl
                      ? { backgroundImage: `url(${trimEditorThumbnailUrl})` }
                      : undefined}
                  />
                  {isChoosingTrimStart ? <span className="trim-unselected-overlay" /> : null}
                  {!isChoosingTrimStart ? (
                    <>
                      <span
                        className="trim-excluded-region is-before"
                        style={{
                          width: `${trimEditor.duration
                            ? (trimEditor.startSeconds / trimEditor.duration) * 100
                            : 0}%`,
                        }}
                      />
                      <span
                        className="trim-excluded-region is-after"
                        style={{
                          left: `${trimEditor.duration
                            ? (provisionalTrimEndSeconds / trimEditor.duration) * 100
                            : 100}%`,
                        }}
                      />
                      <span
                        className={classNames(
                          'trim-bracket-selection',
                          isChoosingTrimEnd && 'is-provisional',
                        )}
                        style={{
                          left: `${trimEditor.duration
                            ? (trimEditor.startSeconds / trimEditor.duration) * 100
                            : 0}%`,
                          width: `${trimEditor.duration
                            ? ((provisionalTrimEndSeconds - trimEditor.startSeconds)
                              / trimEditor.duration) * 100
                            : 0}%`,
                        }}
                      />
                    </>
                  ) : null}
                  {isChoosingTrimEnd ? (
                    <span
                      className="trim-pending-bracket is-start"
                      style={{
                        left: `${trimEditor.duration
                          ? (trimEditor.startSeconds / trimEditor.duration) * 100
                          : 0}%`,
                      }}
                    />
                  ) : null}
                  {isTrimTimelineHovered
                    && !isTrimSelectionComplete
                    && !hasPlayableTrimSelection ? (
                    <span
                      className="trim-hover-playhead"
                      style={{
                        left: `${trimEditor.duration
                          ? (trimHoverTime / trimEditor.duration) * 100
                          : 0}%`,
                      }}
                    />
                  ) : null}
                  {hasPlayableTrimSelection ? (
                    <span
                      className="trim-playback-playhead"
                      style={{
                        left: `${trimEditor.duration
                          ? (Math.min(
                              trimPlaybackEndSeconds,
                              Math.max(trimEditor.startSeconds, trimPreviewTime),
                            ) / trimEditor.duration) * 100
                          : 0}%`,
                      }}
                    />
                  ) : null}
                  {isTrimSelectionComplete ? (
                    <>
                      <input
                        aria-label="Trim start bracket"
                        className={classNames(
                          'trim-bracket-input is-start',
                          activeTrimBracket === 'start' && 'is-active',
                        )}
                        disabled={trimEditor.duration <= 0}
                        max={trimEditor.duration}
                        min="0"
                        onChange={(event) => updateTrimStart(event.currentTarget.value)}
                        onFocus={() => setActiveTrimBracket('start')}
                        onInput={(event) => updateTrimStart(event.currentTarget.value)}
                        onKeyDown={(event) => handleTrimBracketKeyDown(event, 'start')}
                        onPointerDown={() => setActiveTrimBracket('start')}
                        step="0.01"
                        type="range"
                        value={trimEditor.startSeconds}
                      />
                      <input
                        aria-label="Trim end bracket"
                        className={classNames(
                          'trim-bracket-input is-end',
                          activeTrimBracket === 'end' && 'is-active',
                        )}
                        disabled={trimEditor.duration <= 0}
                        max={trimEditor.duration}
                        min="0"
                        onChange={(event) => updateTrimEnd(event.currentTarget.value)}
                        onFocus={() => setActiveTrimBracket('end')}
                        onInput={(event) => updateTrimEnd(event.currentTarget.value)}
                        onKeyDown={(event) => handleTrimBracketKeyDown(event, 'end')}
                        onPointerDown={() => setActiveTrimBracket('end')}
                        step="0.01"
                        type="range"
                        value={trimEditor.endSeconds}
                      />
                    </>
                  ) : null}
                </div>
              </div>
            </div>

            <div className="trim-editor-actions">
              <button className="secondary-button" onClick={closeTrimEditor} type="button">
                Cancel
              </button>
              <button
                className="primary-button"
                disabled={!canSaveTrimSelection}
                onClick={saveTrimmedClip}
                type="button"
              >
                {canSaveTrimSelection ? <Check size={17} /> : null}
                {trimEditor.mode === 'edit' ? 'Save trim' : 'Add to timeline'}
              </button>
            </div>
          </section>
        </div>
      ) : null}
      {isCoverPickerOpen && draftCoverPosition ? (
        <div
          className="cover-picker-backdrop"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setIsCoverPickerOpen(false);
          }}
          role="presentation"
        >
          <section
            aria-labelledby="cover-picker-title"
            aria-modal="true"
            className="cover-picker-dialog"
            role="dialog"
          >
            <header className="cover-picker-header">
              <div>
                <span>Cover image</span>
                <h2 id="cover-picker-title">Choose a frame</h2>
              </div>
              <button
                aria-label="Close cover image picker"
                className="icon-button cover-picker-close"
                onClick={() => setIsCoverPickerOpen(false)}
                type="button"
              >
                <X size={18} />
              </button>
            </header>
            <div
              className="cover-picker-preview is-portrait"
              style={{ aspectRatio: '1080 / 1920' }}
            >
              <div
                className="cover-picker-video-box"
                style={{ width: '100%', height: '100%' }}
              >
                <video
                  key={timelineClipId(draftCoverPosition.clip)}
                  muted
                  onLoadedMetadata={(event) => {
                    hydrateClipMetadata(
                      sourceClipKey(draftCoverPosition.clip),
                      videoElementMetadata(event.currentTarget),
                    );
                    event.currentTarget.currentTime = draftCoverPosition.timeSeconds;
                  }}
                  playsInline
                  preload="auto"
                  ref={coverPickerVideoRef}
                  src={draftCoverPosition.clip.url}
                  style={{
                    objectFit: 'cover',
                    objectPosition: `${boundedPercent(coverCropXPercent)}% center`,
                  }}
                />
              </div>
            </div>
            <div className="cover-picker-meta">
              <strong>{draftCoverPosition.clip.name}</strong>
              <span>
                Clip {coverTimeline.findIndex(
                  (item) => timelineClipId(item.clip) === timelineClipId(draftCoverPosition.clip),
                ) + 1}
                {' of '}{coverTimeline.length} · {formatSeconds(draftCoverPosition.timeSeconds)}
              </span>
            </div>
            <div className="cover-clip-map" aria-hidden="true">
              {coverTimeline.map((entry) => (
                <span
                  className={classNames(
                    timelineClipId(entry.clip) === timelineClipId(draftCoverPosition.clip)
                      && 'is-active',
                  )}
                  key={timelineClipId(entry.clip)}
                  style={{ flexGrow: entry.duration }}
                  title={entry.clip.name}
                />
              ))}
            </div>
            <label className="cover-picker-slider">
              <span>First clip</span>
              <input
                aria-label="Frame across selected clips"
                max={Math.max(0, coverTimelineSeconds - (1 / 30))}
                min="0"
                onChange={(event) => setCoverPickerOffset(Number(event.currentTarget.value))}
                onInput={(event) => setCoverPickerOffset(Number(event.currentTarget.value))}
                step={1 / 30}
                type="range"
                value={draftCoverPosition.offset}
              />
              <span>Last clip</span>
            </label>
            <label className="slider-row clip-axis-row cover-axis-row">
              <span>X Axis</span>
              <input
                aria-label="Cover horizontal position"
                max="100"
                min="0"
                onChange={(event) => setCoverCropXPercent(
                  boundedPercent(Number(event.currentTarget.value)),
                )}
                onInput={(event) => setCoverCropXPercent(
                  boundedPercent(Number(event.currentTarget.value)),
                )}
                type="range"
                value={boundedPercent(coverCropXPercent)}
              />
              <span>{Math.round(boundedPercent(coverCropXPercent))}%</span>
            </label>
            <footer className="cover-picker-actions">
              <button
                className="secondary-button"
                onClick={() => setIsCoverPickerOpen(false)}
                type="button"
              >
                Cancel
              </button>
              <button className="primary-button" onClick={saveCoverFrame} type="button">
                <Check size={18} />
                Done
              </button>
            </footer>
          </section>
        </div>
      ) : null}
    </div>
  );
}

export default App;
