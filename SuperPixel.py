import cv2
import numpy as np
import scipy.stats as stats
from scipy import ndimage
from skimage import feature, filters, exposure, img_as_float

# PARAMS:

hog_args = {}
hog_args["block_norm"] = "L2-Hys"
hog_args["pixels_per_cell"] = (8, 8)
hog_args["cells_per_block"] = (3, 3)
hog_args["orientations"] = 9

lbp_args = {}
lbp_args["P"] = 40
lbp_args["R"] = 5
lbp_args["method"] = "ror"

class SuperPixel:

	def __init__(self, id_num, src_img, lbl_img, mask_img, avg_size):

		self.checkSuperPixel(src_img)

		self.id = id_num
		self.size = avg_size
		self.bounds = self.getBoundingBox(mask_img)

		self.checkBounds()

		self.mask = self.cropMask(mask_img)
		self.features = self.generateFeatures(src_img)

		self.label = self.findLabel(lbl_img)

	# get the min and max coordinates of the superpixel in the input image
	def getBoundingBox(self, mask_img):

		mask = (mask_img == self.id)
		height, width = mask_img.shape

		min_extent = [0,0]
		max_extent = [0,0]

		for i in range(height):
			if (mask[i,:]).any():
				max_extent[0] = i
				if min_extent[0] == 0:
					min_extent[0] = i
			elif max_extent[0] != 0:
				break
		for j in range(width):
			if (mask[:,j]).any():
				max_extent[1] = j
				if min_extent[1] == 0:
					min_extent[1] = j
			elif max_extent[1] != 0:
				break

		del(mask)
		return tuple(min_extent), tuple(max_extent)

	# crop the mask to bounds
	def cropMask(self, mask_img):
		row_min, col_min = self.bounds[0]
		row_max, col_max = self.bounds[1]
		msk = mask_img[row_min:row_max, col_min:col_max]
		mask = (msk == self.id)
		return mask

	# crop and resize the source image to the correct size for feature description
	def processImg(self, src_img, theta):

		row_min, col_min = self.bounds[0]
		row_max, col_max = self.bounds[1]
		roi = src_img[row_min:row_max, col_min:col_max]
		roi = cv2.resize(roi, self.size)

		rotated = ndimage.rotate(roi, theta, reshape=False)

		return rotated

	def generateFeatures(self, src_img):

		features = []

		for theta in range(-40, 60, 20): # will generate angles of -40, -20, 0, 20, 40 degrees
			roi = self.processImg(src_img, theta)
			gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)

			hog = feature.hog(gray, **hog_args)

			lbp = feature.local_binary_pattern(gray, **lbp_args)
			lbp_n_bins = int(lbp.max() + 1)
			lbp_hist, _ = np.histogram(lbp, normed=True, bins=lbp_n_bins, range=(0, lbp_n_bins))

			hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
			r_hist, _ = np.histogram(hsv[:,:,0], bins=64)
			g_hist, _ = np.histogram(hsv[:,:,1], bins=64)
			b_hist, _ = np.histogram(hsv[:,:,2], bins=64)
			hist = np.concatenate((r_hist, g_hist, b_hist))

			features.append(hog)
			features.append(lbp_hist)
			features.append(hist)

			# try using blur as input to edge detection
			# blur = cv2.GaussianBlur(gray, 5, 0)
			# or could downsample instead
			# downsampled = cv2.resize(gray, (30, 30), cv2.INTER_AREA)

			real, imag = filters.gabor(gray, frequency=0.4)
			gabor = real.flatten()

			scharr = filters.scharr(gray)
			scharr = scharr.flatten()

			# features.append(gabor)

			# TODO: append other features to list
			# TODO: try SIFT or ORB as a feature

			# print("hog size: %d" % hog.size)
			# print("lbp size: %d" % lbp_hist.size)
			# print("lbp image size: %d" % lbp.size)
			# print("hist size: %d" % hist.size)
			# print("gabor size: %d" % gabor.size)
			# print("scharr size: %d" % scharr.size)
			#
			# print("hog shape: " + str(hog.shape))
			# print("lbp shape: " + str(lbp_hist.shape))
			# print("lbp image shape: " + str(lbp.shape))
			# print("hist shape: " + str(hist.shape))
			# print("gabor shape: " + str(gabor.shape))
			# print("scharr shape: " + str(scharr.shape))

		result = np.concatenate(features)
		# result = np.squeeze(result)
		# result = np.asarray(features).flatten()
		return result

	# given the image of all labels, find the label for this superpixel
	def findLabel(self, lbl_img):

		row_min, col_min = self.bounds[0]
		row_max, col_max = self.bounds[1]
		roi = lbl_img[row_min:row_max, col_min:col_max]

		roi = roi[np.where(self.mask == True)]
		mode = stats.mode(roi, axis=None)
		mode = mode[0][0]

		del(roi)
		return mode

	#######################
	## HELPER FUNCTIONS: ##
	#######################

	def checkSuperPixel(self, img):
		if not img.any():
			raise ValueError("No input image data given.")
		if len(img.shape) != 3:
			raise ValueError("Misshapen image.")
		if img.shape[0] <= 20:
			raise ValueError("Input image too short.")
		if img.shape[1] <= 20:
			raise ValueError("Input image too narrow.")
		if img.shape[2] != 3:
			raise ValueError("Not enough channels in input image.")

	def checkBounds(self):
		row_min, col_min = self.bounds[0]
		row_max, col_max = self.bounds[1]

		if not (0 <= row_min < row_max):
			raise ValueError("No region found.")
		if not (0 <= col_min < col_max):
			raise ValueError("No region found.")
