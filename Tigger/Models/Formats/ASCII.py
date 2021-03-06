# -*- coding: utf-8 -*-
#
#% $Id$
#
#
# Copyright (C) 2002-2011
# The MeqTree Foundation &
# ASTRON (Netherlands Foundation for Research in Astronomy)
# P.O.Box 2, 7990 AA Dwingeloo, The Netherlands
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>,
# or write to the Free Software Foundation, Inc.,
# 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA
#

import sys
import traceback
import math
import  numpy

import Kittens.utils

from Tigger.Models import ModelClasses
from Tigger.Models import SkyModel
from Tigger import Coordinates
import Tigger.Models.Formats
from Tigger.Models.Formats import dprint,dprintf


DefaultDMSFormat = dict(name=0,
    ra_h=1,ra_m=2,ra_s=3,dec_d=4,dec_m=5,dec_s=6,
    i=7,q=8,u=9,v=10,spi=11,rm=12,emaj_s=13,emin_s=14,pa_d=15,
    freq0=16,tags=slice(17,None));

DefaultDMSFormatString = "name ra_h ra_m ra_s dec_d dec_m dec_s i q u v spi rm emaj_s emin_s pa_d freq0 tags...";

FormatHelp = """
ASCII files are treated as columns of whitespace-separated values. The order
of the columns is determined by a format string, which can be specified in
the first line of the file (prefixed by "#format:"), or supplied by the
user.  Note that in subsequent lines the "#" character is treated as a
comment delimiter, everything following a "#" is ignored.

The format string contains a simple list of field names, such as "name ra_d
dec_d i".  Fields with unrecognized names are simply ignored -- a good way
to skip over unwanted columns is to use a name like 'dummy'.

The following field names are recognized. Note that only a subset of these
needs to be present (as a minimum, coordinates and I flux needs to be
supplied, but the rest is optional):

name:             source name
ra_{rad,d,h,m,s}: R.A. or R.A. component,
                  (in radians, degrees, hours, minutes or seconds)
dec_{rad,d,m,s}:  declination or declination component
dec_sign:         declination sign (+ or -)
i,q,u,v:          IQUV fluxes
pol_frac:         linear polarization fraction 
                  (will interpret both "0.1" and "10%" correctly)
pol_pa_{rad,d}:   linear polarization angle
rm:               rotation measure (freq0 must be supplied as well)
spi:              spectral index (freq0 must be supplied as well)
spi2,3,4...:      spectral curvature
freq0:            reference frequency, for rm and/or spi
emaj_{rad,d,m,s}: source extent, major axis (for Gaussian sources)
emin_{rad,d,m,s}: source extent, minor axis (for Gaussian sources)
pa_{rad,d}:       position angle (for Gaussian sources)
tags:             comma-separated source tags
""";

DEG = math.pi/180;

def load (filename,format=None,freq0=None,center_on_brightest=False,min_extent=0,**kw):
  """Imports an ASCII table
  The 'format' argument can be either a dict (such as the DefaultDMSFormat dict above), or a string such as DefaultDMSFormatString.
  (Other possible field names are "ra_d", "ra_rad", "dec_rad", "dec_sign".)
  If None is specified, DefaultDMSFormat is used.
  The 'freq0' argument supplies a default reference frequency (if one is not contained in the file.)
  If 'center_on_brightest' is True, the mpodel field center will be set to the brightest source.
  'min_extent' is minimal source extent (in radians), above which a source will be treated as a Gaussian rather than a point component.
  """
  srclist = [];
  dprint(1,"importing ASCII DMS file",filename);
  # brightest source and its coordinates
  maxbright = 0;
  brightest_name = radec0 = None;

  # now process file line-by-line
  linenum = 0;
  format_str = ''
  for line in file(filename):
    # for the first line, firgure out the file format
    if not linenum:
      if not format and line.startswith("#format:"):
        format = line[len("#format:"):].strip();
        dprint(1,"file contains format header:",format);
      # set default format
      if format is None:
        format = DefaultDMSFormatString;
      # is the format a string rather than a dict? Turn it into a dict then
      if isinstance(format,str):
        format_str = format;
        # make list of fieldname,fieldnumber tuples
        fields = [ (field,i) for i,field in enumerate(format.split()) ];
        if not fields:
          raise ValueError,"illegal format string in file: '%s'"%format;
        # last fieldname can end with ... to indicate that it absorbs the rest of the line
        if fields[-1][0].endswith('...'):
          fields[-1] = (fields[-1][0][:-3],slice(fields[-1][1],None));
        # make format dict
        format = dict(fields);
      elif not isinstance(format,dict):
        raise TypeError,"invalid 'format' argument of type %s"%(type(format))
        nf = max(format.itervalues())+1;
        fields = ['---']*nf;
        for field,number in format.iteritems():
          fields[number] = field;
        format_str = " ".join(fields);
      # get minimum necessary fields from format
      name_field = format.get('name',None);
      # flux
      try:
        i_field = format['i'];
      except KeyError:
        raise ValueError,"ASCII format specification lacks mandatory flux field ('i')";
      # main RA field
      if 'ra_h' in format:
        ra_field,ra_scale = format['ra_h'],(math.pi/12);
      elif 'ra_d' in format:
        ra_field,ra_scale = format['ra_d'],(math.pi/180);
      elif 'ra_rad' in format:
        ra_field,ra_scale = format['ra_rad'],1.;
      else:
        raise ValueError,"ASCII format specification lacks mandatory Right Ascension field ('ra_h', 'ra_d' or 'ra_rad')";
      # main Dec field
      if 'dec_d' in format:
        dec_field,dec_scale = format['dec_d'],(math.pi/180);
      elif 'dec_rad' in format:
        dec_field,dec_scale = format['dec_rad'],1.;
      else:
        raise ValueError,"ASCII format specification lacks mandatory Declination field ('dec_d' or 'dec_rad')";
      # polarization as QUV
      try:
        quv_fields = [ format[x] for x in ['q','u','v'] ];
      except KeyError:
        quv_fields = None;
      # linear polarization as fraction and angle
      polfrac_field = format.get('pol_frac',None);
      if polfrac_field is not None:
        polpa_field,polpa_scale = format.get('pol_pa_d',None),(math.pi/180);
        if not polpa_field is not None:
          polpa_field,polpa_scale = format.get('pol_pa_rad',None),1;
      # fields for extent parameters
      ext_fields = [];
      for ext in 'emaj','emin','pa':
        for field,scale in (ext,1.),(ext+"_rad",1.),(ext+'_d',DEG),(ext+'_m',DEG/60),(ext+'_s',DEG/3600):
          if field in format:
            ext_fields.append((format[field],scale));
            break;
      # if not all three accumulated, ignore
      if len(ext_fields) != 3:
        ext_fields = None;
      # fields for reference freq and RM and SpI
      freq0_field = format.get('freq0',None);
      rm_field = format.get('rm',None);
      spi_field = format.get('spi',None);
      spi2_field = [ format.get('spi%d'%i,None) for i in range(2,10) ];
      tags_slice = format.get('tags',None);
    # now go on to process the line
    linenum += 1;
    try:
      # strip whitespace
      line = line.strip();
      dprintf(4,"%s:%d: read line '%s'\n",filename,linenum,line);
      # skip empty or commented lines
      if not line or line[0] == '#':
        continue;
      # split (at whitespace) into fields
      fields = line.split();
      # get  name
      name = fields[name_field] if name_field is not None else str(len(srclist)+1);
      i = float(fields[i_field]);
      # get position: RA
      ra = float(fields[ra_field]);
      if 'ra_m' in format:
        ra += float(fields[format['ra_m']])/60.;
      if 'ra_s' in format:
        ra += float(fields[format['ra_s']])/3600.;
      ra *= ra_scale;
      # position: Dec. Separate treatment of sign
      dec = abs(float(fields[dec_field]));
      if 'dec_m' in format:
        dec += float(fields[format['dec_m']])/60.;
      if 'dec_s' in format:
        dec += float(fields[format['dec_s']])/3600.;
      if fields[format.get('dec_sign',dec_field)][0] == '-':
        dec = -dec;
      dec *= dec_scale;
      # see if we have freq0
      try:
        f0 = freq0 or (freq0_field and float(fields[freq0_field]));
      except IndexError:
        f0 = None;
      # set model refrence frequency
      if f0 is not None and freq0 is None:
        freq0 = f0;
      # see if we have Q/U/V
      q=u=v=None;
      if quv_fields:
        try:
          q,u,v = map(float,[fields[x] for x in quv_fields]);
        except IndexError:
          pass;
      if polfrac_field is not None:
        pf = fields[polfrac_field];
        pf = float(pf[:-1])/100 if pf.endswith("%") else float(pf);
        ppa = float(fields[polpa_field])*polpa_scale if polpa_field is not None else 0;
        q = i*pf*math.cos(2*ppa);
        u = i*pf*math.sin(2*ppa);
        v = 0;
      # see if we have RM as well. Create flux object (unpolarized, polarized, polarized w/RM)
      if q is None:
        flux = ModelClasses.Polarization(i,0,0,0);
      elif f0 is None or rm_field is None or rm_field >= len(fields):
        flux = ModelClasses.Polarization(i,q,u,v);
      else:
        flux = ModelClasses.PolarizationWithRM(i,q,u,v,float(fields[rm_field]),f0);
      # see if we have a spectral index
      if f0 is None or spi_field is None or spi_field >= len(fields):
        spectrum = None;
      else:
        spi = [ float(fields[spi_field]) ] + \
              [ (float(fields[x]) if x is not None else 0) for x in spi2_field ];
        # if any higher-order spectral terms are specified, include them here
        # but trim off all trailing zeroes
        while len(spi)>1 and not spi[-1]:
          del spi[-1];
        if len(spi) == 1:
          spi = spi[0];
        spectrum = ModelClasses.SpectralIndex(spi,f0);
      # see if we have extent parameters
      ex=ey=pa=0;
      if ext_fields:
        try:
          ex,ey,pa = [ float(fields[num])*scale for num,scale in ext_fields ];
        except IndexError:
          pass;
      # form up shape object
      if (ex or ey) and max(ex,ey) >= min_extent:
        shape = ModelClasses.Gaussian(ex,ey,pa);
      else:
        shape = None;
      # get tags
      tagdict = {};
      if tags_slice:
        try:
          tags = fields[tags_slice];
        except IndexError:
          pass;
        for tagstr1 in tags:
          for tagstr in tagstr1.split(","):
            if tagstr[0] == "+":
              tagname,value = tagstr[1:],True;
            elif tagstr[0] == "-":
              tagname,value = tagstr[1:],False;
            elif "=" in tagstr:
              tagname,value = tagstr.split("=",1);
              if value[0] in "'\"" and value[-1] in "'\"":
                value = value[1:-1];
              else:
                try:
                  value = float(value);
                except:
                  continue;
            else:
              tagname,value = tagstr,True;
            tagdict[tagname] = value;

      # OK, now form up the source object
      # position
      pos = ModelClasses.Position(ra,dec);
      # now create a source object
      dprint(3,name,ra,dec,i,q,u,v);
      src = SkyModel.Source(name,pos,flux,shape=shape,spectrum=spectrum,**tagdict);
      srclist.append(src);
      # check if it's the brightest
      brightness = src.brightness();
      if brightness > maxbright:
        maxbright = brightness;
        brightest_name = src.name;
        radec0 = ra,dec;
    except:
      dprintf(0,"%s:%d: %s, skipping\n",filename,linenum,str(sys.exc_info()[1]));
  dprintf(2,"imported %d sources from file %s\n",len(srclist),filename);
  # create model
  model = ModelClasses.SkyModel(*srclist);
  if freq0 is not None:
    model.setRefFreq(freq0);
  # set model format
  model.setAttribute("ASCII_Format",format_str);
  # setup model center
  if center_on_brightest and radec0:
    dprintf(2,"brightest source is %s (%g Jy) at %f,%f\n",brightest_name,maxbright,*radec0);
    model.setFieldCenter(*radec0);
  # setup radial distances
  projection = Coordinates.Projection.SinWCS(*model.fieldCenter());
  for src in model.sources:
    l,m = projection.lm(src.pos.ra,src.pos.dec);
    src.setAttribute('r',math.sqrt(l*l+m*m));
  return model;


def save (model,filename,sources=None,format=None,**kw):
  """
  Exports model to a text file
  """;
  if sources is None:
    sources = model.sources;
  dprintf(2,"writing %d model sources to text file %s\n",len(sources),filename);
  # create catalog parser based on either specified format, or the model format, or the default format
  format_str = format or getattr(model,'ASCII_Format',DefaultDMSFormatString);
  dprint(2,"format string is",format_str);
  # convert this into format dict
  fields = [ [field,i] for i,field in enumerate(format_str.split()) ];
  if not fields:
    raise ValueError,"illegal format string '%s'"%format;
  # last fieldname can end with ... ("tags..."), so strip it
  if fields[-1][0].endswith('...'):
    fields[-1][0] = fields[-1][0][:-3];
  # make format dict
  format = dict(fields);
  nfields = len(fields);
  # get minimum necessary fields from format
  name_field = format.get('name',None);
  # main RA field
  ra_rad_field,ra_d_field,ra_h_field,ra_m_field,ra_s_field = \
    [ format.get(x,None) for x in 'ra_rad','ra_d','ra_h','ra_m','ra_s' ];
  dec_rad_field,dec_d_field,dec_m_field,dec_s_field = \
    [ format.get(x,None) for x in 'dec_rad','dec_d','dec_m','dec_s' ];
  if ra_h_field is not None:
    ra_scale = 15;
    ra_d_field = ra_h_field;
  else:
    ra_scale = 1;
  # fields for reference freq and RM and SpI
  freq0_field = format.get('freq0',None);
  rm_field = format.get('rm',None);
  spi_field = format.get('spi',None);
  tags_field = format.get('tags',None);
  # open file
  ff = open(filename,mode="wt");
  ff.write("#format: %s\n"%format_str);
  # write sources
  nsrc = 0;
  for src in sources:
    # only write points and gaussians
    if src.shape is not None and not isinstance(src.shape,ModelClasses.Gaussian):
      dprint(3,"skipping source '%s': non-supported type '%s'"%(src.name,src.shape.typecode));
      continue;
    # prepare field values
    fval = ['0']*nfields;
    # name
    if name_field is not None:
      fval[name_field] = src.name;
    # position: RA
    ra,dec = src.pos.ra,src.pos.dec;
    # RA in radians
    if ra_rad_field is not None:
      fval[ra_rad_field] = str(ra);
    ra /= ra_scale;
    # RA in h/m/s or d/m/s
    if ra_m_field is not None:
      ra,ram,ras = src.pos.ra_hms_static(ra,scale=180,prec=1e-4);
      fval[ra_m_field] = str(ram);
      if ra_s_field is not None:
        fval[ra_s_field] = str(ras);
      if ra_d_field is not None:
        fval[ra_d_field] = str(ra);
    elif ra_d_field is not None:
        fval[ra_d_field] = str(ra*180/math.pi);
    # position: Dec
    if dec_rad_field is not None:
      fval[dec_rad_field] = str(dec);
    if dec_m_field is not None:
      dsign,decd,decm,decs = src.pos.dec_sdms();
      fval[dec_m_field] = str(decm);
      if dec_s_field is not None:
        fval[dec_s_field] = str(decs);
      if dec_d_field is not None:
        fval[dec_d_field] = dsign+str(decd);
    elif dec_d_field is not None:
        fval[dec_d_field] = str(dec*180/math.pi);
    # fluxes
    for stokes in "IQUV":
      field = format.get(stokes.lower());
      if field is not None:
        fval[field] = str(getattr(src.flux,stokes,0));
    # fractional polarization
    if 'pol_frac' in format:
      i,q,u = [ getattr(src.flux,stokes,0) for stokes in "IQU" ];
      fval[format['pol_frac']] = str(math.sqrt(q*q+u*u)/i);
      pa = math.atan2(u,q)/2;
      for field,scale in ('pol_pa_rad',1.),('pol_pa_d',DEG):
        ifield = format.get(field);
        if ifield is not None:
          fval[ifield] = str(pa/scale);
    # shape
    if src.shape:
      for parm,sparm in ("emaj","ex"),("emin","ey"),("pa","pa"):
        for field,scale in (parm,1.),(parm+'_rad',DEG),(parm+'_d',DEG),(parm+'_m',DEG/60),(parm+'_s',DEG/3600):
          ifield = format.get(field.lower());
          if ifield is not None:
            fval[ifield] = str(getattr(src.shape,sparm,0)/scale);
    # RM, spi, freq0
    if freq0_field is not None:
      freq0 = (src.spectrum and getattr(src.spectrum,'freq0',None)) or getattr(src.flux,'freq0',0);
      fval[freq0_field] = str(freq0);
    if rm_field is not None:
      fval[rm_field] = str(getattr(src.flux,'rm',0));
    if spi_field is not None and hasattr(src,'spectrum'):
      fval[spi_field] = str(getattr(src.spectrum,'spi',0));
    # tags
    if tags_field is not None:
      outtags = [];
      for tag,value in src.getTags():
        if isinstance(value,str):
          outtags.append("%s=\"%s\""%(tag,value));
        elif isinstance(value,bool):
          if value:
            outtags.append("+"+tag);
          else:
            outtags.append("-"+tag);
        elif isinstance(value,(int,float)):
          outtags.append("%s=%f"%(tag,value));
      fval[tags_field] = ",".join(outtags);
    # write the line
    ff.write(" ".join(fval)+"\n");
    nsrc += 1;

  ff.close();
  dprintf(1,"wrote %d sources to file %s\n",nsrc,filename);


Tigger.Models.Formats.registerFormat("ASCII",load,"ASCII table",(".txt",".lsm"),export_func=save);
