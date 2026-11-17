import { Clapperboard } from 'lucide-react';
import TimelineEditor from './App.jsx';

// Home reads this list. To add a tool, append an entry:
// { id, name, description, icon, route, component }.
// `route` is a real URL, so a refresh or direct link opens that tool.
// `component` receives `{ onHome }` and should offer a way back.
export const tools = [
  {
    id: 'timeline-editor',
    name: 'Timeline Video Editor',
    description: 'Arrange clips on a timeline, add text and music, and render a short-form video.',
    icon: Clapperboard,
    route: '/tools/timeline',
    component: TimelineEditor,
  },
];
