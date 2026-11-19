// Dragging a queued issue's card within Queued, which changes the order its project's queued and waiting issues start
// in. A card that offers Move Up or Move Down drags by its row. While it drags, a line on the card under the pointer
// shows where it lands among its own project's issues in the order, and the drop posts one /api/place with that place,
// which the server moves at once and the store keeps. The browser's own drag and drop runs it, so a drag never clicks
// the card it started on.
import { deskOf, draw, post } from './app-state.js'

// The card being dragged, and the line that shows where it would land: the card the line sits on, its edge, and the
// place from 1 a drop sends.
let dragged = null
let line = null

/** Whether a card stands for a queued or waiting issue, which has a place in its project's start order. */
export function inStartOrder(card) {
  return card.stage === 'queued' && /^(queued|waiting)-/.test(card.key)
}

/** Whether a card drags: a queued or waiting issue with another in its project's order to trade places with. */
export function canDrag(card) {
  return inStartOrder(card) && card.actions.some(action => action === 'up' || action === 'down')
}

/**
 * Where `moving` lands when dropped on `target`'s top half, or its bottom half when `below`: the place from 1 it takes
 * among `ordered`, its project's issues in the start order, and the edge of `target` the line shows on. Null when the
 * drop does nothing: on the card itself, on a card of another project or one outside the order, or at the place
 * `moving` holds already.
 */
export function dropAt(ordered, moving, target, below) {
  if (target.id === moving.id || target.desk !== moving.desk || !inStartOrder(target)) {
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

/** A project's queued and waiting issue cards, in its start order, as its board lists them. */
function orderOf(desk) {
  return (deskOf(desk)?.cards ?? []).filter(inStartOrder)
}

/** What a drop on `card` would do, from the pointer's height in its row, or null for nothing. */
function dropOver(event, card) {
  if (dragged === null) {
    return null
  }
  const box = event.currentTarget.getBoundingClientRect()
  return dropAt(orderOf(dragged.desk), dragged, card, event.clientY > box.top + box.height / 2)
}

function showLine(next) {
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
 * Answers a drag entering or passing over a row: where a drop lands, it takes the drop, which the HTML spec asks of
 * both events, and shows the line; anywhere else it hides the line and leaves the browser to refuse the drop.
 */
function overRow(event, card) {
  const next = dropOver(event, card)
  if (next !== null) {
    event.preventDefault()
    event.dataTransfer.dropEffect = 'move'
  }
  showLine(next)
}

/**
 * The attributes a list row takes for dragging: every row answers a drag passing over it, which shows or hides the
 * line, and only a card that drags starts one.
 */
export function dragProps(card) {
  return {
    draggable: canDrag(card) ? 'true' : undefined,
    onDragStart: event => {
      if (!canDrag(card)) {
        return
      }
      dragged = card
      line = null
      event.dataTransfer.effectAllowed = 'move'
      event.dataTransfer.setData('text/plain', `#${card.number}`)
      draw()
    },
    onDragEnter: event => overRow(event, card),
    onDragOver: event => overRow(event, card),
    onDragLeave: event => {
      if (line?.id === card.id && !event.currentTarget.contains(event.relatedTarget)) {
        showLine(null)
      }
    },
    onDrop: event => {
      const landing = dropOver(event, card)
      const moving = dragged
      event.preventDefault()
      dragged = null
      line = null
      draw()
      if (landing !== null) {
        void post('/api/place', { desk: moving.desk, key: moving.key, to: landing.place })
      }
    },
    onDragEnd: () => {
      dragged = null
      line = null
      draw()
    },
  }
}
