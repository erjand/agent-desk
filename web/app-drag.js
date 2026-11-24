// Dragging an issue's card within its group: in Queued, which changes the order its project's queued and waiting
// issues start in, and in Open, Blocked, Needs Design, Backlog, and Ideas, which changes its project's list order. A
// card that offers Move Up or Move Down drags by its row once the pointer, pressed on it, moves a few pixels. While it
// drags, a line on the card under the pointer shows where it lands among its own project's issues in its group, and
// releasing the pointer posts one move, which the server makes at once and the store keeps: /api/place with the place
// from 1 in Queued, and /api/list-place with the issue it lands right above or below in the other groups. Pointer
// events run the drag instead of the browser's own drag and drop, which takes the mousewheel away: the wheel scrolls
// the list as it always does, a pointer held near the list's top or bottom edge scrolls it toward that edge, and the
// line follows the card the scroll brings under a still pointer. Escape cancels a drag, and the click that ends one
// never selects the card.
import { deskOf, draw, post } from './app-state.js'

// How far, in pixels, a pressed pointer moves before the press becomes a drag; less stays a click.
const DRAG_PX = 4
// How near, in pixels, the pointer comes to the list's top or bottom edge to scroll it, and the most it scrolls in a
// frame.
export const EDGE_PX = 48
const EDGE_STEP = 16

// The press that may become a drag: the card, its row, the list holding it, and where the pointer went down. Then the
// card being dragged, the line that shows where it would land (the card it sits on, its edge, its number, and the
// place from 1 a drop in Queued sends), and where the pointer last was.
let pressed = null
let dragged = null
let line = null
let last = null
// Whether the click that follows the pointer's release belongs to a drag, which selects nothing.
let swallow = false
// The animation frame that scrolls the list while the pointer sits at its edge.
let edgeFrame = null

/** Whether a card stands for a queued or waiting issue, which has a place in its project's start order. */
export function inStartOrder(card) {
  return card.stage === 'queued' && /^(queued|waiting)-/.test(card.key)
}

// The prefix of the row key of an issue with a place in its project's list order, by the group its card shows in.
// Blocked's waits on a stacked PR's base take keys of their own, and hold no place.
const LIST_KEYS = { open: 'next-', blocked: 'blocked-', design: 'decide-', backlog: 'backlog-', idea: 'idea-' }

/** Whether a card stands for an issue with a place in the list order: one in Open, Blocked, Needs Design, Backlog, or
 * Ideas. */
export function inListOrder(card) {
  return Object.hasOwn(LIST_KEYS, card.stage) && String(card.key).startsWith(LIST_KEYS[card.stage])
}

/** Whether a card has a place its group lets a drag change: in the start order or the list order. */
function hasPlace(card) {
  return inStartOrder(card) || inListOrder(card)
}

/** Whether a card drags: an issue with a place in its group's order, and another of its project's to trade with. */
export function canDrag(card) {
  return hasPlace(card) && card.actions.some(action => action === 'up' || action === 'down')
}

/**
 * Where `moving` lands when dropped on `target`'s top half, or its bottom half when `below`: the place from 1 it takes
 * among `ordered`, its project's issues in its group's order, and the edge of `target` the line shows on. Null when
 * the drop does nothing: on the card itself, on a card of another project or group or one outside the order, or at the
 * place `moving` holds already.
 */
export function dropAt(ordered, moving, target, below) {
  if (target.id === moving.id || target.desk !== moving.desk || target.stage !== moving.stage || !hasPlace(target)) {
    return null
  }
  const from = ordered.findIndex(card => card.id === moving.id)
  const at = ordered.findIndex(card => card.id === target.id)
  if (from < 0 || at < 0) {
    return null
  }
  // The slot between cards the line marks, counted with `moving` still in its place, which leaving shifts up by one.
  const slot = below ? at + 1 : at
  const index = slot > from ? slot - 1 : slot
  return index === from ? null : { id: target.id, edge: below ? 'below' : 'above', place: index + 1 }
}

/** The cards of `card`'s project in its group's order, as its board lists them. */
function orderOf(card) {
  return (deskOf(card.desk)?.cards ?? []).filter(one => one.stage === card.stage && hasPlace(one))
}

/**
 * What a drop posts, as [path, body], for `moving` landing where `line` shows: its place from 1 in Queued, and the
 * issue it lands right above or below in the list order's groups.
 */
export function dropPost(moving, line) {
  if (inStartOrder(moving)) {
    return ['/api/place', { desk: moving.desk, key: moving.key, to: line.place }]
  }
  return ['/api/list-place', { desk: moving.desk, key: moving.key,
    [line.edge === 'above' ? 'before' : 'after']: line.number }]
}

/** What a drop at the point (`x`, `y`) would do, from the list row there and the pointer's height in it, or null. */
function dropOver(x, y) {
  const row = document.elementFromPoint(x, y)?.closest?.('.list [data-key]')
  const cards = orderOf(dragged)
  const target = row ? cards.find(card => card.id === row.dataset.key) : undefined
  if (target === undefined) {
    return null
  }
  const box = row.getBoundingClientRect()
  const at = dropAt(cards, dragged, target, y > box.top + box.height / 2)
  return at && { ...at, number: target.number }
}

/** Moves the line to where a drop at the pointer's last place would land, and draws the list when it moved. */
function follow() {
  const next = dragged !== null && last !== null ? dropOver(last.x, last.y) : null
  if (next?.id !== line?.id || next?.edge !== line?.edge) {
    line = next
    draw()
  }
}

/** The class a list row adds while a drag is under way: the dragged card's, or the drop line's edge on its target. */
export function dragClass(card) {
  return dragged?.id === card.id ? 'dragging' : line?.id === card.id ? `drop-${line.edge}` : ''
}

/**
 * How far the list in `box` scrolls this frame with the pointer at (`x`, `y`): up near or above its top edge, down near
 * or below its bottom edge, faster the nearer the edge, and not at all elsewhere or to either side of the list.
 */
export function edgeStep(box, x, y) {
  if (x < box.left || x > box.right) {
    return 0
  }
  const into = y < box.top + EDGE_PX ? y - box.top - EDGE_PX : y > box.bottom - EDGE_PX ? y - box.bottom + EDGE_PX : 0
  return Math.sign(into) * Math.ceil(Math.min(Math.abs(into), EDGE_PX) / EDGE_PX * EDGE_STEP)
}

/**
 * Scrolls the list a step toward the edge the pointer sits at, and again next frame while it stays there and the list
 * still moves; the pointer's next move starts it again.
 */
function edgeScroll() {
  edgeFrame = null
  if (dragged === null || last === null || pressed.list === null) {
    return
  }
  const step = edgeStep(pressed.list.getBoundingClientRect(), last.x, last.y)
  const from = pressed.list.scrollTop
  pressed.list.scrollTop += step
  if (pressed.list.scrollTop !== from) {
    edgeFrame = requestAnimationFrame(edgeScroll)
  }
}

function onMove(event) {
  if (event.pointerId !== pressed.pointerId) {
    return
  }
  last = { x: event.clientX, y: event.clientY }
  if (dragged === null) {
    if (Math.hypot(event.clientX - pressed.x, event.clientY - pressed.y) < DRAG_PX) {
      return
    }
    dragged = pressed.card
    swallow = true
    pressed.row.setPointerCapture?.(event.pointerId)
    document.getSelection?.()?.removeAllRanges()
    document.documentElement.classList.add('card-drag')
    draw()
  }
  follow()
  if (edgeFrame === null) {
    edgeScroll()
  }
}

function onUp(event) {
  if (event.pointerId !== pressed.pointerId) {
    return
  }
  const landing = dragged !== null ? line : null
  const moving = dragged
  finish(false)
  if (landing !== null) {
    void post(...dropPost(moving, landing))
  }
}

/** Ends a press the browser takes back, such as for a touch scroll, and drops nothing. */
function cancel(event) {
  if (event.pointerId === pressed.pointerId) {
    finish(false)
  }
}

/** Escape ends a drag and drops nothing, and the button's release that follows still selects nothing. */
function onKey(event) {
  if (event.key === 'Escape' && dragged !== null) {
    event.preventDefault()
    event.stopPropagation()
    finish(true)
  }
}

/** Lets clicks select again once the click that follows this task's release, if any, has come. */
function unswallow() {
  setTimeout(() => {
    swallow = false
  })
}

/**
 * Forgets the press and any drag, and draws the list without its line. A click that ends a drag comes after the
 * release, so the flag that swallows it outlives the drag until that click or the task after the release, which
 * `down` says is still to come.
 */
function finish(down = false) {
  const wasDragging = dragged !== null
  document.removeEventListener('pointermove', onMove, true)
  document.removeEventListener('pointerup', onUp, true)
  document.removeEventListener('pointercancel', cancel, true)
  document.removeEventListener('keydown', onKey, true)
  document.removeEventListener('scroll', follow, true)
  if (edgeFrame !== null) {
    cancelAnimationFrame(edgeFrame)
    edgeFrame = null
  }
  pressed = null
  dragged = null
  line = null
  last = null
  if (wasDragging) {
    document.documentElement.classList.remove('card-drag')
    if (down) {
      document.addEventListener('pointerup', unswallow, { capture: true, once: true })
    } else {
      unswallow()
    }
    draw()
  }
}

/**
 * The attributes a list row takes for dragging: a card that drags marks itself so it shows a grab cursor and starts a
 * press on the primary button, and every row swallows the click that ends a drag.
 */
export function dragProps(card) {
  return {
    'data-drags': canDrag(card) ? 'true' : undefined,
    onPointerDown: event => {
      // A press whose release the page never saw, as when the window lost focus mid-press, ends here.
      if (pressed !== null) {
        finish(false)
      }
      if (!canDrag(card) || event.button !== 0) {
        return
      }
      swallow = false
      const row = event.currentTarget
      pressed = { card, row, list: row.closest('.list'), pointerId: event.pointerId, x: event.clientX,
        y: event.clientY }
      document.addEventListener('pointermove', onMove, true)
      document.addEventListener('pointerup', onUp, true)
      document.addEventListener('pointercancel', cancel, true)
      document.addEventListener('keydown', onKey, true)
      // The list scrolls under a still pointer on the wheel or at its edge, which brings another card under it.
      document.addEventListener('scroll', follow, true)
    },
    onClickCapture: event => {
      if (swallow) {
        swallow = false
        event.stopImmediatePropagation()
        event.preventDefault()
      }
    },
  }
}
