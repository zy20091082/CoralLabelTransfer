import os
import time
import glob

import yaml
import pickle
import joblib
from tqdm import tqdm

import multiprocessing as mp
import itertools as it

import numpy as np
import cv2

from sklearn.multiclass import OneVsRestClassifier
from sklearn.svm import SVC

from SuperPixel import SuperPixel
from Preprocessor import Preprocessor, Reducers
from labeling_utils import *

def main():

	# Setup
	config = init()

	# Read images:
	print("Reading mosaic...")
	mosaic, mosaic_annotated = get_mosaic(config.mosaic_images)
	images = get_images(config.img_dir)

	if config.mode == filemode.WRITE:

		# Segment mosaic
		mosaic_features, mosaic_labels, avg_size, label_db = get_mosaic_features(mosaic, mosaic_annotated, config.mosaic_superpixels, config.processors)
		print("Average size: ", avg_size)

		# Setup preprocessor
		preprocessor = get_preprocessor(config.preprocessor, mosaic_features)
		mosaic_features = preprocessor.process(mosaic_features)

		# Save important information
		save_info(preprocessor, avg_size, label_db, config.save["info"])

		# Train SVM
		classifier = train_svm(mosaic_features, mosaic_labels, config.svm_params, config.processors)
		save_classifier(classifier, config.save["svm"], config.save["svm_compression"])

	else:

		# Load info and classifier
		print("Loading info...")
		global preprocessor
		preprocessor, avg_size, label_db = load_info(config.save["info"])

		print("Loading classifier...")
		global classifier
		classifier = joblib.load(config.save["svm"])

	# Setup args
	print("Loading args for classification...")
	shared = Namespace(
		avg_size = avg_size,
		label_db = label_db,
		spixel_config = config.superpixel,
		masks_dir = config.save["masks_dir"],
	)
	args = zip(images, it.repeat(shared))

	# Classify
	print("Starting classification...")
	threadpool = mp.Pool(config.processors)
	masks = threadpool.starmap(classify, args)
	threadpool.close()

#####################
## HELPER METHODS: ##
#####################

def get_mosaic_features(img, mask, spixel_config, processors):

	## OVERSEGMENT: ##
	print("Segmenting...")
	spixel_args = (spixel_config["approx_num_superpixels"], spixel_config["num_levels"], spixel_config["iterations"])
	segment_mask, num_spixels = oversegment(img, spixel_args)

	## GET AVG SUPERPIXEL SIZE: ##
	print("Calculating average superpixel shape...")
	avg_size = calc_avg_size(segment_mask, int(num_spixels/25))

	## EXTRACT SUPERPIXELS: ##
	print("Computing features and creating SuperPixels...")

	threadpool = mp.Pool(processors)
	args = zip(range(num_spixels), it.repeat(img), it.repeat(mask), it.repeat(segment_mask), it.repeat(avg_size))
	args = tqdm(args, total=num_spixels)
	superpixels = threadpool.starmap(create_spixel, args)
	threadpool.close()

	## FORMAT DATA: ##
	print("Formatting data...")
	features = [pixel.features for pixel in superpixels if pixel is not None]
	labels = [pixel.id for pixel in superpixels if pixel is not None]
	id_label_db = {pixel.id: pixel.label for pixel in superpixels}

	features = np.array(features)
	labels = np.array(labels)

	return features, labels, avg_size, id_label_db

def classify(img_fn, shared):
	print("Classifying...")

	## READ IMAGE: ##
	img = cv2.imread(img_fn)

	## OVERSEGMENT: ##
	print("\tSegmenting...")
	spixel_args = (shared.spixel_config["approx_num_superpixels"], shared.spixel_config["num_levels"], shared.spixel_config["iterations"])
	segment_mask, num_spixels = oversegment(img, spixel_args)

	## EXTRACT SUPERPIXELS: ##
	print("\tCreating superpixels...")
	superpixels = [create_spixel(i, img, None, segment_mask, shared.avg_size) for i in range(num_spixels)]

	## FORMAT FEATURES: ##
	features = [pixel.features for pixel in superpixels if pixel is not None]
	features = np.array(features)

	labels = [pixel.id for pixel in superpixels if pixel is not None]
	invalid_spixels = superpixels.count(None)
	if invalid_spixels > 0:
		print("\tFound {} invalid superpixels".format(invalid_spixels))

	## PREPROCESS FEATURES: ##
	print("\tPreprocessing...")
	features = preprocessor.process(features)

	## PREDICT CLASSES FOR FEATURES: ##
	print("\tPredicting...")
	pred = classifier.predict(features)
	predictions = [shared.label_db[p] for p in pred]

	## DEBUG: ##
	# print("\tPrinting...")
	# print(pred)
	# print(predictions)
	# print(shared.label_db)

	## LABEL THE IMAGE: ##
	print("\tGenerating mask...")
	mask = np.zeros(segment_mask.shape, dtype=np.uint8)
	for i, l in enumerate(labels):
		if predictions[i] != 0:
			mask[np.where(segment_mask == l)] = predictions[i]

	## WRITE OUT MASK: ##
	mask_path = get_mask_filepath(shared.masks_dir, img_fn)
	cv2.imwrite(mask_path, mask)

	return mask

def create_spixel(*args):
	try:
		pixel = SuperPixel(*args)
		return pixel
	except ValueError as err:
		tqdm.write("ERROR: Skipping SuperPixel. " + str(err))

def get_mosaic(image_config):
	mosaic_fn = image_config["image"]
	mosaic = cv2.imread(mosaic_fn)

	mask_fn = image_config["annotation"]
	mask = cv2.imread(mask_fn, 0)

	return mosaic, mask

def get_images(image_dir):
	pattern = os.path.join(image_dir, "*.JPG")
	filelist = glob.glob(pattern)
	return filelist

def get_mask_filepath(dir, img_fn):
	base_fn = img_fn.split('/')[-1].split('.')[0]
	fn = base_fn + "_mask.png"
	path = os.path.join(dir, fn)
	return path

def get_preprocessor(config, features):
	print("Fitting preprocessor...")
	preprocessor = Preprocessor(normalize=config["normalize"],
								reduce_features=config["reduce_features"],
								reducer_type=config["reducer_type"],
								explained_variance=config["explained_variance"])
	preprocessor.train(features)
	return preprocessor

def train_svm(features, labels, params, n_processors):

	print("Training SVM...")
	start_time = time.time()

	svm = SVC(**params)
	classifier = OneVsRestClassifier(svm, n_jobs=n_processors)
	classifier.fit(features, labels)

	end_time = time.time()
	elapsed_time = end_time - start_time
	print("Training took %.1f seconds" % elapsed_time)

	return classifier

def save_classifier(classifier, filename, compression):
	print("Saving SVM...")
	start_time = time.time()

	svm_file = open(filename, 'wb')
	joblib.dump(classifier, svm_file, compress=compression)
	svm_file.close()

	end_time = time.time()
	elapsed_time = end_time - start_time
	print("Saving SVM took %.1f seconds" % elapsed_time)

def save_info(preprocessor, avg_size, label_db, filename):
	info = (preprocessor, avg_size, label_db)
	info_file = open(filename, 'wb')
	pickle.dump(info, info_file)

def load_info(filename):
	info_file = open(filename, 'rb')
	preprocessor, avg_size, label_db = pickle.load(info_file)
	return preprocessor, avg_size, label_db

#####################
## CONFIG METHODS: ##
#####################

def init():
	# get config
	config = init_config()

	# make directory for output
	if config.mode == filemode.WRITE:
		try:
			os.mkdir(config.save["dir"])
			os.mkdir(config.save["masks_dir"])
		except FileExistsError:
			print("Error. Version {} already exists.".format(config.version))
			exit()

	# write config file
	with open(config.save["config"], 'w') as config_file:
		yaml.dump(config, config_file)

	# setup logging
	global saved_stdout
	saved_stdout = sys.stdout
	log_file = open(config.save["log"], 'w')
	sys.stdout = writer(sys.stdout, log_file)

	return config

def init_config():

	VERSION = 3
	PROCESSORS = 12
	MODE = filemode.READ

	# image files
	images = dict(
		image = "imgs/mosaic.png",
		annotation = "imgs/mosaic_mask.png"
	)
	img_dir = "/home/chei/felix/data/orthophotos/"

	# superpixels
	mosaic_superpixels = dict(
		approx_num_superpixels = 30000,
		num_levels = 5,
		iterations = 100
	)
	src_superpixels = dict(
		approx_num_superpixels = 5000,
		num_levels = 5,
		iterations = 100
	)

	# preprocessor
	preprocessor = dict(
		normalize = True,
		reduce_features = True,
		reducer_type = Reducers.pca,
		explained_variance = 0.98
	)

	# SVM parameter grid
	svm_params = dict(
		kernel = "rbf",
		cache_size = 20000,
		class_weight = "balanced",
		C = 0.1
	)

	# saving
	save = dict(
		dir = "results/v{0:d}/".format(VERSION),
		config = "results/v{0:d}/config.yml".format(VERSION),
		log = "results/v{0:d}/log.txt".format(VERSION),
		svm = "results/v{0:d}/svm.pkl".format(VERSION),
		masks_dir = "results/v{0:d}/masks/".format(VERSION),
		info = "results/v{0:d}/info.pkl".format(VERSION),
		svm_compression = 3
	)

	params = Namespace(
		version = VERSION,
		mode = MODE,
		processors = PROCESSORS,
		mosaic_images = images,
		img_dir = img_dir,
		mosaic_superpixels = mosaic_superpixels,
		superpixel = src_superpixels,
		preprocessor = preprocessor,
		svm_params = svm_params,
		save = save
	)

	return params

if __name__ == "__main__":
	main()
