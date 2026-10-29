"""Fit native schematic pages to their contents without changing the circuit.

PSCAD 4.6.2's embedded canvas form (resource 519) defines five ANSI/ISO
paper choices. Native schematic coordinates use 144 units per inch (18-unit grid).
"""
import argparse
import json
import math
from pathlib import Path
import re
import shutil
import time
import xml.etree.ElementTree as ET

PAPERS = ((1224, 1584), (1584, 2448), (2448, 3168), (3168, 4896), (4896, 6336))
MARGIN = 36
SYMBOL_MARGIN = 72


def _points(item, definitions):
    from .author import transform
    x, y = float(item.get('x', 0)), float(item.get('y', 0))
    points = [(x, y)]
    w, h = float(item.get('w', 0)), float(item.get('h', 0))
    points.extend([(x + w, y + h), (x - SYMBOL_MARGIN, y - SYMBOL_MARGIN),
                   (x + SYMBOL_MARGIN, y + SYMBOL_MARGIN)])
    if item.tag == 'Wire':
        points.extend((x + float(v.get('x', 0)), y + float(v.get('y', 0)))
                      for v in item.findall('vertex'))
    elif item.tag == 'User':
        definition = definitions.get(item.get('defn'))
        if definition is not None:
            orient = int(item.get('orient', 0))
            params = {p.get('name').lower(): p.get('value', '') for p in item.findall('paramlist/param')}
            for port in definition.findall('svg/port'):
                dx, dy = transform(float(port.get('x', 0)), float(port.get('y', 0)), orient)
                points.extend([(x + dx - MARGIN, y + dy - MARGIN),
                               (x + dx + MARGIN, y + dy + MARGIN)])
            for shape in definition.findall('svg/*'):
                try:
                    if shape.tag in {'rect', 'image'}:
                        sx, sy = float(shape.get('x', 0)), float(shape.get('y', 0))
                        corners = [(sx, sy), (sx + float(shape.get('width', 0)), sy + float(shape.get('height', 0)))]
                    elif shape.tag in {'circle', 'ellipse'}:
                        sx, sy = float(shape.get('cx', 0)), float(shape.get('cy', 0))
                        rx, ry = float(shape.get('rx', shape.get('r', 0))), float(shape.get('ry', shape.get('r', 0)))
                        corners = [(sx-rx, sy-ry), (sx+rx, sy+ry)]
                    elif shape.tag == 'line':
                        corners = [(float(shape.get('x1', 0)), float(shape.get('y1', 0))),
                                   (float(shape.get('x2', 0)), float(shape.get('y2', 0)))]
                    else:
                        continue
                except ValueError:
                    continue
                points.extend((x+px, y+py) for px, py in (transform(px, py, orient) for px, py in corners))
            for text in definition.findall('svg/text'):
                try:
                    tx, ty = float(text.get('x', 0)), float(text.get('y', 0))
                except ValueError:
                    continue  # Parameterized graphics still have native bounds/port margins.
                label = re.sub(r'%:?([A-Za-z_][\w]*)',
                               lambda m: params.get(m[1].lower(), m[0]), text.text or '')
                width = sum(18 if ord(c) > 127 else 9 for c in label)
                left = {'middle': -width / 2, 'end': -width}.get(text.get('text-anchor'), 0)
                for px, py in [(tx + left, ty - 18), (tx + left + width, ty + 18)]:
                    dx, dy = transform(px, py, orient)
                    points.append((x + dx, y + dy))
    return points


def fit_tree(root, libraries=()):
    """Grow UserCanvas pages; translate only when contents cross the top/left edge."""
    from .author import definition_sources
    definitions = {scope + ':' + d.get('name'): d for scope, source in
                   definition_sources(root, libraries).items() for d in source.findall('definitions/Definition')}
    changes = {}
    for definition in root.findall('definitions/Definition'):
        canvas = definition.find("schematic[@classid='UserCanvas']")
        if canvas is None:
            continue
        objects = [el for el in canvas if el.get('x') is not None and el.get('y') is not None]
        if not objects:
            continue
        points = [point for item in objects for point in _points(item, definitions)]
        left, top = min(p[0] for p in points), min(p[1] for p in points)
        right, bottom = max(p[0] for p in points), max(p[1] for p in points)
        dx = max(0, math.ceil((MARGIN - left) / 18) * 18)
        dy = max(0, math.ceil((MARGIN - top) / 18) * 18)
        required = (right + dx + MARGIN, bottom + dy + MARGIN)
        pl = canvas.find('paramlist')
        values = {p.get('name'): p for p in pl.findall('param')} if pl is not None else {}
        size = int(values['size'].get('value')) if 'size' in values else 0
        orient = int(values['orient'].get('value')) if 'orient' in values else 1
        chosen = None
        for new_size in range(size, len(PAPERS)):
            for new_orient in (orient, 1 - orient):
                width, height = PAPERS[new_size][::(-1 if new_orient else 1)]
                if required[0] <= width and required[1] <= height:
                    chosen = new_size, new_orient, width, height
                    break
            if chosen:
                break
        if chosen is None:
            raise ValueError('Page %s needs %.0f x %.0f units; exceeds the largest PSCAD paper. '
                             'Split the schematic into modules.' % (definition.get('name'), *required))
        new_size, new_orient, width, height = chosen
        if (new_size, new_orient, dx, dy) == (size, orient, 0, 0):
            continue
        if pl is None:
            pl = ET.SubElement(canvas, 'paramlist')
        for key, value in [('size', new_size), ('orient', new_orient)]:
            if key not in values:
                values[key] = ET.SubElement(pl, 'param', {'name': key})
            values[key].set('value', str(value))
        for item in objects:
            item.set('x', str(int(float(item.get('x')) + dx)))
            item.set('y', str(int(float(item.get('y')) + dy)))
        changes[definition.get('name')] = {'old_paper': [size, orient], 'paper': [new_size, new_orient],
                                          'dimensions': [width, height], 'offset': [dx, dy],
                                          'content_bounds': [left + dx, top + dy, right + dx, bottom + dy]}
    return changes


def fit_file(path, libraries=None, backup=True, check=False):
    """Offline entry for native XML authors as well as create/edit/run tools."""
    path = Path(path)
    if libraries is None:
        plan = path.parent / 'plan.json'
        libraries = json.loads(plan.read_text(encoding='utf-8')).get('libraries', []) if plan.exists() else []
    tree = ET.parse(path)
    changes = fit_tree(tree.getroot(), libraries)
    result = {'changed': bool(changes), 'pages': changes}
    if not changes or check:
        return result
    refs_path = path.parent / 'refs.json'
    refs = json.loads(refs_path.read_text(encoding='utf-8')) if refs_path.exists() else None
    if refs is not None:
        for page, change in changes.items():
            dx, dy = change['offset']
            for item in refs.get(page, {}).values():
                for key, offset in [('x', dx), ('y', dy)]:
                    if key in item:
                        item[key] += offset
                    for port in item.get('ports', {}).values():
                        port[key] += offset
    if backup:
        history = path.parent / 'revisions'
        history.mkdir(exist_ok=True)
        previous = history / ('canvas_before_%d.pscx' % time.time_ns())
        shutil.copy2(path, previous)
        result['backup'] = str(previous)
    ET.indent(tree)
    tree.write(path, encoding='utf-8', xml_declaration=True)
    if refs is not None:
        refs_path.write_text(json.dumps(refs, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path')
    parser.add_argument('--check', action='store_true', help='Report required changes without writing')
    args = parser.parse_args()
    print(json.dumps(fit_file(args.path, check=args.check), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
