arguments:
 number of images, default = 1. autoincrement if file already exist but all files similar!
 resolution, default 256,256
 name default get from path, if no fallback to 1.png
 format default get from path, if no fallback to  png (256 color, max compress)
 grid cell size = 128
 path (filename or folder)
 bg = black. the foreground (for grid.cross, print) calculate as invert(bg)
	add bg setting 'auto' to set Hue across images if number>1.  for example Hue 360/N*i. Sat=max

example path set in argument is A:/1.png number of images 5
script check 1.png ... 5.png in dir A:/
 if found then 1_1.png itc so all files has similar names different only by N counter

print:
	grid
	central small cross exactly w/2 h/2
	filename without whole path in top left
		if resolution less then 32 then instead of name use just a counter