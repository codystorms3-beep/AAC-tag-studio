"""Minimal dependency-free 3MF writer with one colour per part (works with PrusaSlicer, Bambu Studio, Cura, Orca)."""
import zipfile
from xml.sax.saxutils import quoteattr

_CT = ('<?xml version="1.0" encoding="UTF-8"?>'
       '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
       '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
       '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>')
_RELS = ('<?xml version="1.0" encoding="UTF-8"?>'
         '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
         '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
         'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')


def _color(c):
    c = c.lstrip("#").upper()
    return "#" + (c + "FF" if len(c) == 6 else c)


def write_3mf(path, parts, materials):
    """parts: iterable of (name, vertices, faces, material_index); materials: [(name, '#rrggbb'), ...]"""
    L = ['<?xml version="1.0" encoding="UTF-8"?>',
         '<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">',
         '<resources>', '<basematerials id="1">']
    for n, c in materials:
        L.append(f'<base name={quoteattr(n)} displaycolor="{_color(c)}"/>')
    L.append('</basematerials>')
    count = 0
    for i, (name, verts, faces, mat) in enumerate(parts, start=2):
        count += 1
        L.append(f'<object id="{i}" type="model" pid="1" pindex="{int(mat)}" name={quoteattr(name)}><mesh><vertices>')
        for v in verts:
            L.append(f'<vertex x="{float(v[0]):.4f}" y="{float(v[1]):.4f}" z="{float(v[2]):.4f}"/>')
        L.append('</vertices><triangles>')
        for f in faces:
            L.append(f'<triangle v1="{int(f[0])}" v2="{int(f[1])}" v3="{int(f[2])}"/>')
        L.append('</triangles></mesh></object>')
    L.append('</resources><build>')
    for i in range(2, 2 + count):
        L.append(f'<item objectid="{i}"/>')
    L.append('</build></model>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CT)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("3D/3dmodel.model", "\n".join(L))
