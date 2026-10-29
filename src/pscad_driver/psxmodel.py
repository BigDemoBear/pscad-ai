# -*- coding: utf-8 -*-
"""psxmodel.py — `.pscx` 的读取 / 改参 / 结构校验（不启动 PSCAD）。

本模块用于离线读取结构、修改参数和编辑 XML 对象。完整工程装配可使用
author 模块的原生 API；特殊结构可直接编辑原生 XML。

`.pscx` 结构：
  <project name version Target>
    <definitions>
      <Definition classid="UserCmpDefn" name="Main">
        <paramlist>…</paramlist>
        <svg>…</svg>                       # 端口几何在这里（<port x y dim mode>）
        <schematic classid="UserCanvas">
          <User classid="UserCmp" defn="master:resistor" id x y w h orient q>
            <paramlist link crc><param name value /></paramlist>
          </User>
          <Wire classid="WireOrthogonal" id x y w h orient>
            <vertex x y />
          </Wire>
        </schematic>
      </Definition>
    </definitions>
    <hierarchy>…</hierarchy>
  </project>
"""
import os
import xml.etree.ElementTree as ET

PAGE = "Main"


class Psx:
    def __init__(self, path):
        self.path = path
        self.tree = ET.parse(path)
        self.root = self.tree.getroot()

    # ---------------------------------------------------------------- 读取
    @property
    def name(self):
        return self.root.get("name")

    @property
    def version(self):
        return self.root.get("version")

    def page(self, name=PAGE):
        for d in self.root.iter("Definition"):
            if d.get("name") == name:
                sch = d.find("schematic")
                if sch is None:
                    sch = ET.SubElement(d, "schematic", {"classid": "UserCanvas"})
                return sch
        raise KeyError("工程里没有页面 %s" % name)

    def instances(self, page=PAGE):
        out = []
        for u in self.page(page).findall("User"):
            pl = u.find("paramlist")
            params = {}
            if pl is not None:
                for p in pl.findall("param"):
                    params[p.get("name")] = p.get("value")
            out.append({
                "id": u.get("id"),
                "defn": (u.get("defn") or "").split(":")[-1],
                "raw_defn": u.get("defn"),
                "x": int(u.get("x", 0)), "y": int(u.get("y", 0)),
                "w": int(u.get("w", 0)), "h": int(u.get("h", 0)),
                "orient": int(u.get("orient", 0)),
                "params": params,
                "_el": u,
            })
        return out

    def wires(self, page=PAGE):
        """所有线对象。WireOrthogonal 有顶点；Bus/TLine/Cable 只有包围盒。"""
        out = []
        for w in self.page(page):
            if w.tag != "Wire":
                continue
            x, y = int(w.get("x", 0)), int(w.get("y", 0))
            ww, hh = int(w.get("w", 0)), int(w.get("h", 0))
            pts = [(x + int(v.get("x", 0)), y + int(v.get("y", 0)))
                   for v in w.findall("vertex")]
            out.append({"id": w.get("id"), "classid": w.get("classid"),
                        "pts": pts, "box": (x, y, ww, hh), "_el": w})
        return out

    def find(self, *, iid=None, defn=None, page=PAGE):
        for inst in self.instances(page):
            if iid is not None and inst["id"] != str(iid):
                continue
            if defn is not None and inst["defn"] != defn.split(":")[-1]:
                continue
            return inst
        return None

    def settings(self):
        """工程级设置（仿真时长、步长等）。"""
        for pl in self.root.iter("paramlist"):
            if pl.get("name") == "Settings":
                return {p.get("name"): p.get("value") for p in pl.findall("param")}
        return {}

    # ---------------------------------------------------------------- 改参
    def set_params(self, iid, params):
        inst = self.find(iid=iid)
        if inst is None:
            raise KeyError("没有这个元件 id：%s" % iid)
        pl = inst["_el"].find("paramlist")
        if pl is None:
            pl = ET.SubElement(inst["_el"], "paramlist")
        existing = {p.get("name"): p for p in pl.findall("param")}
        changed = {}
        for k, v in params.items():
            if k in existing:
                old = existing[k].get("value")
                existing[k].set("value", str(v))
                changed[k] = [old, str(v)]
            else:
                ET.SubElement(pl, "param", {"name": k, "value": str(v)})
                changed[k] = [None, str(v)]
        return changed

    def set_wire_params(self, wid, params):
        """改线对象自身携带的参数（TLine 的长度/名称等存在嵌套 User 的 paramlist 里）。"""
        for w in self.page():
            if w.tag == "Wire" and str(w.get("id")) == str(wid):
                u = w.find("User")
                if u is None:
                    raise KeyError("线 %s 里没有嵌套参数块" % wid)
                pl = u.find("paramlist")
                if pl is None:
                    pl = ET.SubElement(u, "paramlist")
                existing = {q.get("name"): q for q in pl.findall("param")}
                changed = {}
                for k, v in params.items():
                    if k in existing:
                        changed[k] = [existing[k].get("value"), str(v)]
                        existing[k].set("value", str(v))
                    else:
                        ET.SubElement(pl, "param", {"name": k, "value": str(v)})
                        changed[k] = [None, str(v)]
                return changed
        raise KeyError("没有这条线：%s" % wid)

    def lines(self, page=PAGE):
        """所有 TLine 对象及其长度。"""
        out = []
        for w in self.page(page):
            if w.tag == "Wire" and w.get("classid") == "TLine":
                u = w.find("User")
                params = {}
                if u is not None and u.find("paramlist") is not None:
                    params = {q.get("name"): q.get("value")
                              for q in u.find("paramlist").findall("param")}
                out.append({"id": w.get("id"), "name": w.get("name"),
                            "defn": w.get("defn"), "params": params})
        return out

    def remove_instance(self, iid):
        el = self.find(iid=iid)["_el"]
        self.page().remove(el)

    # ---------------------------------------------------------------- 落盘
    def save(self, path=None):
        path = path or self.path
        ET.indent(self.tree, space="  ")
        self.tree.write(path, encoding="utf-8", xml_declaration=False)
        return path

    # ---------------------------------------------------------------- 校验
    def structure(self, page=PAGE):
        us = self.instances(page)
        ws = self.wires(page)
        return {
            "project": self.name, "version": self.version,
            "instances": len(us), "wires": len(ws),
            "defs": len([d for d in self.root.iter("Definition")]),
            "by_defn": {d: sum(1 for u in us if u["defn"] == d)
                        for d in sorted({u["defn"] for u in us})},
            "wire_classes": sorted({w["classid"] for w in ws}),
        }


def roundtrip(src, dst):
    """读→写→再读，比较语义是否一致（不比空白）。"""
    a = Psx(src)
    sa = a.structure()
    a.save(dst)
    b = Psx(dst)
    sb = b.structure()
    same = all(sa[k] == sb[k] for k in ("instances", "wires", "defs", "by_defn"))
    # 参数逐条比对
    pa = {(i["id"], k): v for i in a.instances() for k, v in i["params"].items()}
    pb = {(i["id"], k): v for i in b.instances() for k, v in i["params"].items()}
    return {"same_structure": same, "same_params": pa == pb,
            "a": sa, "b": sb, "param_count": len(pa)}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Inspect a native PSCAD project")
    parser.add_argument("project")
    args = parser.parse_args()
    print(Psx(args.project).structure())
