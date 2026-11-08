import { useCallback, useState, type ReactNode } from 'react';
import { closestCenter, DndContext, KeyboardSensor, PointerSensor, useSensor, useSensors, type DragEndEvent } from '@dnd-kit/core';
import { arrayMove, SortableContext, sortableKeyboardCoordinates, useSortable, verticalListSortingStrategy } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { GripHorizontal } from 'lucide-react';
import { useTranslation } from 'react-i18next';

/** Order saved in this browser, per user. Unknown ids are dropped and new ones appended,
 *  so a stale or edited value can never hide a block. */
export function loadOrder(storageKey: string, defaults: string[]): string[] {
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey) ?? 'null');
    if (Array.isArray(saved)) {
      const known = saved.filter((id): id is string => typeof id === 'string' && defaults.includes(id));
      return [...new Set(known), ...defaults.filter((id) => !known.includes(id))];
    }
  } catch {
    /* storage unavailable or corrupt: fall back to the default order */
  }
  return defaults;
}

function Block({ id, children }: { id: string; children: ReactNode }) {
  const { t } = useTranslation();
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, transform, transition, isDragging } = useSortable({ id });
  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={`group relative ${isDragging ? 'z-10 opacity-80 shadow-xl' : ''}`}
    >
      <button
        type="button"
        ref={setActivatorNodeRef}
        aria-label={t('dashboard.dragBlock')}
        title={t('dashboard.dragBlock')}
        className="absolute -top-3 left-1/2 z-20 flex -translate-x-1/2 cursor-grab touch-none items-center rounded-full border bg-background px-3 py-0.5 text-muted-foreground opacity-0 shadow-sm transition-opacity group-hover:opacity-100 focus-visible:opacity-100 active:cursor-grabbing [@media(hover:none)]:opacity-100"
        {...attributes}
        {...listeners}
      >
        <GripHorizontal className="size-4" />
      </button>
      {children}
    </div>
  );
}

type Props = {
  storageKey: string;
  defaults: string[];
  blocks: Record<string, ReactNode>;
};

/** Vertical list of dashboard blocks that can be reordered by drag and drop (pointer or
 *  keyboard) through the handle of each block; the order is saved in localStorage. */
export function SortableBlocks({ storageKey, defaults, blocks }: Props) {
  const { t } = useTranslation();
  const [order, setOrder] = useState(() => loadOrder(storageKey, defaults));
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );
  const save = useCallback(
    (next: string[]) => {
      setOrder(next);
      try {
        localStorage.setItem(storageKey, JSON.stringify(next));
      } catch {
        /* storage unavailable: the order just won't survive a reload */
      }
    },
    [storageKey],
  );
  const visible = order.filter((id) => blocks[id]);
  const onDragEnd = (e: DragEndEvent) => {
    const { active, over } = e;
    if (!over || active.id === over.id) return;
    const from = order.indexOf(String(active.id));
    const to = order.indexOf(String(over.id));
    if (from >= 0 && to >= 0) save(arrayMove(order, from, to));
  };
  const changed = order.join() !== defaults.join();

  return (
    <div className="space-y-6">
      {changed && (
        <div className="flex justify-end">
          <button type="button" onClick={() => save(defaults)} className="text-xs text-muted-foreground hover:text-foreground hover:underline">
            {t('dashboard.resetLayout')}
          </button>
        </div>
      )}
      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
        <SortableContext items={visible} strategy={verticalListSortingStrategy}>
          <div className="space-y-6">
            {visible.map((id) => (
              <Block key={id} id={id}>
                {blocks[id]}
              </Block>
            ))}
          </div>
        </SortableContext>
      </DndContext>
    </div>
  );
}
