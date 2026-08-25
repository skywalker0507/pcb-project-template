const project=await eda.dmt_Project.getCurrentProjectInfo();
const pcb=await eda.dmt_Pcb.getCurrentPcbInfo();
const documentInfo=await eda.dmt_SelectControl.getCurrentDocumentInfo();
if(!project||!pcb||!documentInfo){throw new Error("An EasyEDA project and PCB document must be active");}
const plain=(value)=>{try{return JSON.parse(JSON.stringify(value));}catch(_){return value;}};
const layerItems=await eda.pcb_Layer.getAllLayers();
const layers=[];
for(const item of layerItems||[]){const value=plain(item);const id=value.layerId??value.id;if(id!==undefined){layers.push(id);}}
const components=[];
for(const item of await eda.pcb_PrimitiveComponent.getAll()||[]){
  const value=plain(item);const primitiveId=value.primitiveId??value.id??"";const pads=[];
  if(primitiveId){for(const pin of await eda.pcb_PrimitiveComponent.getAllPinsByPrimitiveId(primitiveId)||[]){
    const p=plain(pin);const pad=Array.isArray(p.pad)?p.pad:[];let shape=String(pad[0]??"round").toLowerCase();let width=0;let height=0;
    if(typeof pad[1]==="number"){width=pad[1]||0;height=typeof pad[2]==="number"?(pad[2]||width):width;}
    else if(Array.isArray(pad[1])){const nums=pad[1].filter((n)=>typeof n==="number");if(nums.length>=2){const xs=[];const ys=[];for(let i=0;i+1<nums.length;i+=2){xs.push(nums[i]);ys.push(nums[i+1]);}width=Math.max(...xs)-Math.min(...xs);height=Math.max(...ys)-Math.min(...ys);shape="rect";}}
    const hole=Array.isArray(p.hole)?p.hole:null;
    // EasyEDA exposes pad geometry in mil but hole tuples in 0.01 inch
    // (10 mil) units. The official KiRouting model expects drill in mil.
    const drill=hole&&hole.length>=2?(hole[1]||0)*10:0;
    pads.push({primitiveId:p.primitiveId??p.id??"",number:String(p.padNumber??p.number??""),x:p.x??0,y:p.y??0,net:p.net??"",layer:p.layer??value.layer??1,shape,width,height,pad,drill,hole,padType:p.padType??null,metallization:p.metallization??null,maskAndPasteExpansion:p.solderMaskAndPasteMaskExpansion??null,rotation:p.rotation??0});
  }}
  const properties=value.otherProperty&&typeof value.otherProperty==="object"?value.otherProperty:{};
  const footprint=value.footprint&&typeof value.footprint==="object"?value.footprint:{};
  components.push({primitiveId,x:value.x??0,y:value.y??0,designator:value.designator??value.name??"",layer:value.layer??1,rotation:value.rotation??0,pads,identity:{name:value.name??null,footprint:footprint.name??properties.Footprint??null,partIdentity:properties.partnumber??properties["Manufacturer Part Number"]??properties.mpn??null,manufacturer:properties.manufacturer??value.manufacturer??null,manufacturerId:value.manufacturerId??null,supplier:value.supplier??null,supplierId:properties.lcsc_id??properties.lcsc??value.supplierId??null}});
}
const allLines=await eda.pcb_PrimitiveLine.getAll()||[];const existing_tracks=[];const outlineLines=[];
for(const item of allLines){const value=plain(item);if((value.layer??0)===11){outlineLines.push({startX:value.startX??0,startY:value.startY??0,endX:value.endX??0,endY:value.endY??0});}else if(value.net){existing_tracks.push({net:value.net,layer:value.layer??1,startX:value.startX??0,startY:value.startY??0,endX:value.endX??0,endY:value.endY??0,width:value.lineWidth??value.width??0});}}
const existing_vias=[];
for(const item of await eda.pcb_PrimitiveVia.getAll()||[]){const value=plain(item);if(value.net){existing_vias.push({net:value.net,x:value.x??0,y:value.y??0,holeDiameter:value.holeDiameter??0,diameter:value.diameter??0,startLayer:value.startLayer??1,endLayer:value.endLayer??2});}}
const outlineArcs=[];
for(const item of await eda.pcb_PrimitiveArc.getAll()||[]){const value=plain(item);if((value.layer??0)===11){outlineArcs.push({startX:value.startX??0,startY:value.startY??0,endX:value.endX??0,endY:value.endY??0,arcAngle:value.arcAngle??0});}}
const nets=(await eda.pcb_Net.getAllNetsName()||[]).filter((name)=>name&&!name.startsWith("unconnected-"));
return {source:{project,pcb,document:documentInfo},board:{layers,outline:[],outlineLines,outlineArcs,stackup:[],boardThickness:1.6},components,nets,existing_tracks,existing_vias,routing_config:{routing_mode:"single_ended",nets_to_route:[],units_mm:false}};
