#--------------------------------------------------------
import base64

def set_bit(v, index, x):
    """
        Set the index:th bit of v to 1 if x is truthy,
        else to 0, and return the new value.
    """
    mask = 1 << index   # Compute mask, an integer with just bit 'index' set.
    v &= ~mask          # Clear the bit indicated by the mask (if x is False)
    if x:
        v |= mask         # If x was True, set the bit indicated by the mask.
    return v            # Return the result, we're done.

#--------------------------------------------------------
# Pull the XML 'm' values in - this comes from the measurement portion
wave = ''
offset = 0
gain = 0
hz = 0
points = 0
binwave = []

for m in mg:
      if (m.attrib["name"] == 'Offset'):
            offset = int(m.text)
      elif (m.attrib["name"] == 'Gain'):
            # GAIN is not correct in the XML for pressures
            if (mg.get('name') == 'GE_ART'):
                  gain = 0.25
            elif (mg.get('name') == 'INVP1'):
                  gain = 0.01
            else:
                  gain = float(m.text)
            elif (m.attrib["name"] == 'Wave'):
                  wave = m.text
            elif (m.attrib["name"] == 'Hz'):
                  hz = int(m.text)
            elif (m.attrib["name"] == 'Points'):
                  points = int(m.text)

# Convert the base64 char string from the XML into
# SmallInt (0-255) array
wave = base64.b64decode(wave)

# Convert the SmallInt array into Int values
# pairs of the wave array --> single int value
for i in range (0,len(wave)-1,2):
      t = (wave[i]) + wave[i+1]*256
      # This is dense: left side CLEARS the 15th bit. Right side
      #    substracts -32768 from the number if that bit was '1'
      #    before it was cleared
      # (t >> 15) grabs the last bit (shifts), leaving 1 or 0
      t = set_bit(t,15,0) + (-32768)*(t >> 15)

      # Adjust by gain & offset then add to bin array
      t = t*gain + offset
      binwave.append(t)
